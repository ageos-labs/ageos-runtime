#define _GNU_SOURCE

#include "ageos/hw.h"
#include "ageos/limits.h"
#include "ageos/log.h"
#include "ageos/overfs.h"
#include "ageos/sandbox.h"
#include "ageos/scheduler.h"

#include <errno.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

extern int ageos_landlock_apply_filesystem(const char *writable_dir, int allow_dns);

#define CHECK(cond)                                                                   \
    do {                                                                              \
        if (!(cond)) {                                                                \
            fprintf(stderr, "CHECK failed: %s (%s:%d)\n", #cond, __FILE__, __LINE__); \
            return 1;                                                                 \
        }                                                                             \
    } while (0)

static int make_temp_dir(const char *prefix, char *buffer, size_t buffer_size) {
    int written = snprintf(buffer, buffer_size, "/tmp/%s-XXXXXX", prefix);
    if (written < 0 || (size_t)written >= buffer_size) {
        return -1;
    }
    return mkdtemp(buffer) != NULL ? 0 : -1;
}

static int read_file(const char *path, char *buffer, size_t buffer_size) {
    FILE *fp = fopen(path, "r");
    if (fp == NULL) {
        return -1;
    }
    size_t count = fread(buffer, 1, buffer_size - 1, fp);
    fclose(fp);
    buffer[count] = '\0';
    return 0;
}

static int test_hw_functions(void) {
    uint64_t vram_bytes = 0;
    uint64_t free_vram_bytes = 0;
    CHECK(ageos_hw_total_ram_bytes() > 0);
    vram_bytes = ageos_hw_vram_bytes();
    free_vram_bytes = ageos_hw_free_vram_bytes();
    (void)vram_bytes;
    (void)free_vram_bytes;
    return 0;
}

static int test_limits_function(void) {
    CHECK(ageos_apply_cgroup_limits(NULL) == -EINVAL);
    return 0;
}

static int test_overfs_functions(void) {
    ageos_sandbox_config cfg = {0};
    cfg.rootfs_dir = "";
    CHECK(ageos_overfs_rootfs_enabled(&cfg) == 0);
    cfg.rootfs_dir = "/rootfs";
    CHECK(ageos_overfs_rootfs_enabled(&cfg) == 1);

    char path[256];
    CHECK(ageos_overfs_join_mount_path("/tmp/root", "/etc/hosts", path, sizeof(path)) == 0);
    CHECK(strcmp(path, "/tmp/root/etc/hosts") == 0);
    CHECK(ageos_overfs_join_mount_path("", "/etc/hosts", path, sizeof(path)) == 0);
    CHECK(strcmp(path, "/etc/hosts") == 0);
    CHECK(ageos_overfs_join_mount_path("/tmp/root", "relative", path, sizeof(path)) == -EINVAL);

    char tmpdir[256];
    CHECK(make_temp_dir("ageos-overfs", tmpdir, sizeof(tmpdir)) == 0);

    char nested_dir[512];
    snprintf(nested_dir, sizeof(nested_dir), "%s/a/b/c", tmpdir);
    CHECK(ageos_overfs_mkdir_p(nested_dir, 0755) == 0);
    struct stat st;
    CHECK(stat(nested_dir, &st) == 0 && S_ISDIR(st.st_mode));

    char file_path[512];
    snprintf(file_path, sizeof(file_path), "%s/a/b/c/file.txt", tmpdir);
    CHECK(ageos_overfs_ensure_file(file_path, 0644) == 0);
    CHECK(stat(file_path, &st) == 0 && S_ISREG(st.st_mode));

    CHECK(ageos_overfs_ensure_file(nested_dir, 0644) == -EINVAL);

    char missing_source[512];
    char missing_target[512];
    snprintf(missing_source, sizeof(missing_source), "%s/no-such-file", tmpdir);
    snprintf(missing_target, sizeof(missing_target), "%s/no-such-target", tmpdir);
    CHECK(ageos_overfs_bind_optional_dir_readonly(missing_source, missing_target) == 0);
    CHECK(ageos_overfs_bind_optional_file_readonly(missing_source, missing_target) == 0);

    CHECK(ageos_overfs_bind_file_readonly(missing_source, missing_target) < 0);
    CHECK(ageos_overfs_bind_file_readwrite(missing_source, missing_target) < 0);
    CHECK(ageos_overfs_bind_dir(missing_source, missing_target) < 0);
    CHECK(ageos_overfs_mount_tmpfs_at(NULL, "mode=1777") == -EINVAL);

    int setup_rc = ageos_overfs_setup_mounts(tmpdir, &cfg);
    CHECK(setup_rc <= 0);
    return 0;
}

static int test_scheduler_functions(void) {
    char tmpdir[256];
    CHECK(make_temp_dir("ageos-sched", tmpdir, sizeof(tmpdir)) == 0);
    char state_path[512];
    snprintf(state_path, sizeof(state_path), "%s/scheduler.state", tmpdir);
    CHECK(setenv("AGEOS_SCHEDULER_STATE", state_path, 1) == 0);

    CHECK(ageos_scheduler_configure_limits(0.01, 0.01) == 0);
    CHECK(ageos_scheduler_register_agent(NULL, 123, "/bin/echo", 0, "test") == -1);
    CHECK(ageos_scheduler_register_agent("agent-1", 123, "/bin/echo", 5, "test") == 0);
    CHECK(ageos_scheduler_deregister_agent("agent-1") == 0);

    int allowed = 1;
    char state[64];
    char reason[256];
    CHECK(ageos_scheduler_admit_model_job("spec", "model-a", 10, 5.0, 5.0, NULL, state, sizeof(state), reason, sizeof(reason)) == -1);
    CHECK(ageos_scheduler_admit_model_job("spec", "model-a", 10, 5.0, 5.0, &allowed, state, sizeof(state), reason, sizeof(reason)) == 0);
    CHECK(allowed == 0);
    CHECK(strlen(state) > 0);
    CHECK(strlen(reason) > 0);

    CHECK(ageos_scheduler_mark_model_loaded(NULL, "spec", "backend", 1.0, 0.0, 111, 8080) == -1);
    CHECK(ageos_scheduler_mark_model_loaded("model-a", "spec", "backend", 1.0, 0.0, 111, 8080) == 0);
    CHECK(ageos_scheduler_mark_model_unloaded("model-a") == 0);
    CHECK(ageos_scheduler_evict_model("model-a") == 0);

    CHECK(ageos_scheduler_add_queue_item(NULL, "kind", "spec", "model-a", 1, "reason") == -1);
    CHECK(ageos_scheduler_add_queue_item("job-1", "kind", "spec", "model-a", 1, "reason") == 0);

    char *snapshot = ageos_scheduler_snapshot_json();
    CHECK(snapshot != NULL);
    CHECK(strstr(snapshot, "\"agents\"") != NULL);
    CHECK(strstr(snapshot, "\"queue\"") != NULL);
    ageos_scheduler_free_string(snapshot);

    char *inference_error = ageos_inference_chat_json(NULL);
    CHECK(inference_error != NULL);
    CHECK(strstr(inference_error, "invalid native inference request") != NULL);
    ageos_scheduler_free_string(inference_error);

    CHECK(unsetenv("AGEOS_SCHEDULER_STATE") == 0);
    return 0;
}

static int test_log_functions(void) {
    char tmpdir[256];
    CHECK(make_temp_dir("ageos-log", tmpdir, sizeof(tmpdir)) == 0);
    char log_path[512];
    snprintf(log_path, sizeof(log_path), "%s/test.log", tmpdir);

    CHECK(setenv("AGEOS_LOG_LEVEL", "debug", 1) == 0);
    ageos_log_init();
    ageos_log_set_file(log_path);
    ageos_log_set_level("debug");
    ageos_log_write(AGEOS_LOG_LEVEL_INFO, "test.c", 123, "info message", "hello=%d", 1);
    ageos_log_set_file(NULL);

    char contents[1024];
    CHECK(read_file(log_path, contents, sizeof(contents)) == 0);
    CHECK(strstr(contents, "INFO test.c:123 info message:hello=1") != NULL);
    CHECK(unsetenv("AGEOS_LOG_LEVEL") == 0);
    return 0;
}

static int test_sandbox_and_landlock_functions(void) {
    CHECK(ageos_sandbox_run(NULL) == -EINVAL);
    CHECK(ageos_landlock_apply_filesystem("/", 0) == -EPERM);
    return 0;
}

int main(void) {
    int failed = 0;
    failed |= test_hw_functions();
    failed |= test_limits_function();
    failed |= test_overfs_functions();
    failed |= test_scheduler_functions();
    failed |= test_log_functions();
    failed |= test_sandbox_and_landlock_functions();
    if (failed != 0) {
        return 1;
    }
    printf("all libageos tests passed\n");
    return 0;
}
