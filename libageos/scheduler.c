#include "ageos/scheduler.h"

#include "ageos/hw.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define AGEOS_SCHED_MAGIC 0x4147534348443031ULL
#define AGEOS_SCHED_VERSION 2U
#define AGEOS_MAX_AGENTS 64
#define AGEOS_MAX_MODELS 32
#define AGEOS_MAX_QUEUE 64
#define AGEOS_FIELD_SMALL 64
#define AGEOS_FIELD_MEDIUM 256
#define AGEOS_FIELD_LARGE 512
#define AGEOS_JSON_CAPACITY 65536
#define AGEOS_RAM_LOW_WATERMARK_PERCENT 20.0
#define AGEOS_RAM_NO_ADMIT_WATERMARK_PERCENT 8.0
#define AGEOS_GPU_RESERVED_VRAM_PERCENT 10.0
#define AGEOS_AGENT_UID_BASE 60000U
#define AGEOS_AGENT_UID_END 64000U

typedef struct {
    int active;
    char agent_id[AGEOS_FIELD_SMALL];
    int64_t pid;
    char binary[AGEOS_FIELD_LARGE];
    int niceness;
    char status[AGEOS_FIELD_SMALL];
    char specialty[AGEOS_FIELD_SMALL];
    double registered_at;
} ageos_agent_record;

typedef struct {
    int active;
    char name[AGEOS_FIELD_SMALL];
    char specialty[AGEOS_FIELD_SMALL];
    char backend[AGEOS_FIELD_SMALL];
    double ram_gb;
    double vram_gb;
    int64_t pid;
    int port;
    int refcount;
    double loaded_at;
    double last_used;
} ageos_model_record;

typedef struct {
    int active;
    char job_id[AGEOS_FIELD_SMALL];
    char kind[AGEOS_FIELD_SMALL];
    char specialty[AGEOS_FIELD_SMALL];
    char model_name[AGEOS_FIELD_SMALL];
    int niceness;
    double created_at;
    char reason[AGEOS_FIELD_MEDIUM];
} ageos_queue_record;

typedef struct {
    uint64_t magic;
    uint32_t version;
    uint32_t reserved;
    double ram_limit_gb;
    double vram_limit_gb;
    ageos_agent_record agents[AGEOS_MAX_AGENTS];
    ageos_model_record models[AGEOS_MAX_MODELS];
    ageos_queue_record queue[AGEOS_MAX_QUEUE];
} ageos_scheduler_state;

typedef struct {
    int fd;
    ageos_scheduler_state state;
} ageos_locked_state;

typedef struct {
    char *data;
    size_t len;
    size_t cap;
    int failed;
} ageos_json_builder;

static double ageos_now_seconds(void) {
    return (double)time(NULL);
}

static void copy_field(char *dst, size_t dst_size, const char *src) {
    if (dst_size == 0) {
        return;
    }
    if (src == NULL) {
        dst[0] = '\0';
        return;
    }
    snprintf(dst, dst_size, "%s", src);
}

static int mkdir_if_needed(const char *path) {
    if (mkdir(path, 0700) == 0 || errno == EEXIST) {
        return 0;
    }
    return -1;
}

static int running_in_sandbox_user_namespace(void) {
    uid_t effective_uid = geteuid();
    if (effective_uid >= AGEOS_AGENT_UID_BASE && effective_uid < AGEOS_AGENT_UID_END) {
        return 1;
    }
    FILE *handle = fopen("/proc/self/uid_map", "r");
    if (handle == NULL) {
        if (errno == EACCES && geteuid() == 0) {
            return 1;
        }
        return 0;
    }
    unsigned long inside_uid = 0;
    unsigned long outside_uid = 0;
    unsigned long count = 0;
    int sandboxed = 0;
    while (fscanf(handle, "%lu %lu %lu", &inside_uid, &outside_uid, &count) == 3) {
        if (outside_uid != inside_uid && count == 1) {
            sandboxed = 1;
            break;
        }
    }
    fclose(handle);
    return sandboxed;
}

static int sandbox_scheduler_path(char *buffer, size_t buffer_size) {
    char cwd[512];
    if (getcwd(cwd, sizeof(cwd)) == NULL) {
        return -1;
    }
    char dir[1024];
    snprintf(dir, sizeof(dir), "%s/.ageos", cwd);
    if (mkdir_if_needed(dir) != 0) {
        return -1;
    }
    snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
    return 0;
}

static int scheduler_path(char *buffer, size_t buffer_size) {
    const char *explicit_path = getenv("AGEOS_SCHEDULER_STATE");
    if (explicit_path != NULL && explicit_path[0] != '\0') {
        snprintf(buffer, buffer_size, "%s", explicit_path);
        return 0;
    }

    if (running_in_sandbox_user_namespace()) {
        return sandbox_scheduler_path(buffer, buffer_size);
    }

    const char *runtime_dir = getenv("XDG_RUNTIME_DIR");
    if (runtime_dir != NULL && runtime_dir[0] != '\0') {
        char dir[512];
        snprintf(dir, sizeof(dir), "%s/ageos", runtime_dir);
        if (mkdir_if_needed(dir) != 0) {
            return -1;
        }
        snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
        return 0;
    }

    char dir[512];
    snprintf(dir, sizeof(dir), "/tmp/ageos-%lu", (unsigned long)getuid());
    if (mkdir_if_needed(dir) != 0) {
        return -1;
    }
    snprintf(buffer, buffer_size, "%s/scheduler.state", dir);
    return 0;
}

static void init_state(ageos_scheduler_state *state) {
    memset(state, 0, sizeof(*state));
    state->magic = AGEOS_SCHED_MAGIC;
    state->version = AGEOS_SCHED_VERSION;
}

static int lock_state(ageos_locked_state *locked) {
    char path[1024];
    if (scheduler_path(path, sizeof(path)) != 0) {
        return -1;
    }
    int fd = open(path, O_RDWR | O_CREAT, 0600);
    if (fd < 0) {
        return -1;
    }
    if (flock(fd, LOCK_EX) != 0) {
        close(fd);
        return -1;
    }

    ageos_scheduler_state state;
    ssize_t bytes = pread(fd, &state, sizeof(state), 0);
    if (bytes != (ssize_t)sizeof(state) || state.magic != AGEOS_SCHED_MAGIC || state.version != AGEOS_SCHED_VERSION) {
        init_state(&state);
        if (ftruncate(fd, (off_t)sizeof(state)) != 0) {
            flock(fd, LOCK_UN);
            close(fd);
            return -1;
        }
        if (pwrite(fd, &state, sizeof(state), 0) != (ssize_t)sizeof(state)) {
            flock(fd, LOCK_UN);
            close(fd);
            return -1;
        }
    }

    locked->fd = fd;
    locked->state = state;
    return 0;
}

static int unlock_state(ageos_locked_state *locked, int write_state) {
    int result = 0;
    if (write_state) {
        if (pwrite(locked->fd, &locked->state, sizeof(locked->state), 0) != (ssize_t)sizeof(locked->state)) {
            result = -1;
        }
        fsync(locked->fd);
    }
    flock(locked->fd, LOCK_UN);
    close(locked->fd);
    return result;
}

static int find_agent(ageos_scheduler_state *state, const char *agent_id) {
    for (int i = 0; i < AGEOS_MAX_AGENTS; i++) {
        if (state->agents[i].active && strcmp(state->agents[i].agent_id, agent_id) == 0) {
            return i;
        }
    }
    return -1;
}

static int find_model(ageos_scheduler_state *state, const char *name) {
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        if (state->models[i].active && strcmp(state->models[i].name, name) == 0) {
            return i;
        }
    }
    return -1;
}

static int find_free_agent(ageos_scheduler_state *state) {
    for (int i = 0; i < AGEOS_MAX_AGENTS; i++) {
        if (!state->agents[i].active) {
            return i;
        }
    }
    return -1;
}

static int find_free_model(ageos_scheduler_state *state) {
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        if (!state->models[i].active) {
            return i;
        }
    }
    return -1;
}

static int find_free_queue(ageos_scheduler_state *state) {
    for (int i = 0; i < AGEOS_MAX_QUEUE; i++) {
        if (!state->queue[i].active) {
            return i;
        }
    }
    return -1;
}

static double committed_ram_gb(ageos_scheduler_state *state) {
    double total = 0.0;
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        if (state->models[i].active) {
            total += state->models[i].ram_gb;
        }
    }
    return total;
}

static double committed_vram_gb(ageos_scheduler_state *state) {
    double total = 0.0;
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        if (state->models[i].active) {
            total += state->models[i].vram_gb;
        }
    }
    return total;
}

static double effective_ram_limit_gb(ageos_scheduler_state *state) {
    if (state->ram_limit_gb > 0.0) {
        return state->ram_limit_gb;
    }
    double total_gb = (double)ageos_hw_total_ram_bytes() / 1073741824.0;
    return total_gb > 1.0 ? total_gb : 1.0;
}

static double effective_vram_limit_gb(ageos_scheduler_state *state) {
    if (state->vram_limit_gb > 0.0) {
        return state->vram_limit_gb;
    }
    return (double)ageos_hw_vram_bytes() / 1073741824.0;
}

static const char *ram_state(ageos_scheduler_state *state, double requested_ram_gb) {
    double total_gb = effective_ram_limit_gb(state);
    double remaining_gb = total_gb - committed_ram_gb(state) - requested_ram_gb;
    double remaining_percent = (remaining_gb / total_gb) * 100.0;
    if (remaining_percent < AGEOS_RAM_NO_ADMIT_WATERMARK_PERCENT) {
        return "no_ram";
    }
    if (remaining_percent < AGEOS_RAM_LOW_WATERMARK_PERCENT) {
        return "low";
    }
    return "available";
}

static void terminate_model_process(ageos_model_record *record) {
    if (record->pid > 0) {
        kill((pid_t)record->pid, SIGTERM);
    }
}

static void evict_model_at(ageos_scheduler_state *state, int index) {
    if (index < 0 || index >= AGEOS_MAX_MODELS || !state->models[index].active) {
        return;
    }
    terminate_model_process(&state->models[index]);
    memset(&state->models[index], 0, sizeof(state->models[index]));
}

static int find_lru_idle_model(ageos_scheduler_state *state, const char *except_name) {
    int index = -1;
    double oldest = 0.0;
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        ageos_model_record *item = &state->models[i];
        if (!item->active || item->refcount > 0) {
            continue;
        }
        if (except_name != NULL && strcmp(item->name, except_name) == 0) {
            continue;
        }
        if (index < 0 || item->last_used < oldest) {
            index = i;
            oldest = item->last_used;
        }
    }
    return index;
}

static int has_capacity_for(ageos_scheduler_state *state, double requested_ram_gb, double requested_vram_gb) {
    if (committed_ram_gb(state) + requested_ram_gb > effective_ram_limit_gb(state)) {
        return 0;
    }
    double vram_limit_gb = effective_vram_limit_gb(state);
    if (requested_vram_gb > 0.0 && vram_limit_gb > 0.0 && committed_vram_gb(state) + requested_vram_gb > vram_limit_gb) {
        return 0;
    }
    return 1;
}

static int evict_idle_until_fits(ageos_scheduler_state *state, const char *model_name, double requested_ram_gb, double requested_vram_gb) {
    while (!has_capacity_for(state, requested_ram_gb, requested_vram_gb)) {
        int index = find_lru_idle_model(state, model_name);
        if (index < 0) {
            return 0;
        }
        evict_model_at(state, index);
    }
    return 1;
}

static int add_queue_item_locked(
    ageos_scheduler_state *state,
    const char *job_id,
    const char *kind,
    const char *specialty,
    const char *model_name,
    int niceness,
    const char *reason
) {
    int index = find_free_queue(state);
    if (index < 0) {
        index = 0;
    }
    ageos_queue_record *record = &state->queue[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->job_id, sizeof(record->job_id), job_id);
    copy_field(record->kind, sizeof(record->kind), kind);
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    copy_field(record->model_name, sizeof(record->model_name), model_name);
    record->niceness = niceness;
    record->created_at = ageos_now_seconds();
    copy_field(record->reason, sizeof(record->reason), reason);
    return 0;
}

int ageos_scheduler_configure_limits(double ram_limit_gb, double vram_limit_gb) {
    ageos_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    locked.state.ram_limit_gb = ram_limit_gb > 0.0 ? ram_limit_gb : 0.0;
    locked.state.vram_limit_gb = vram_limit_gb > 0.0 ? vram_limit_gb : 0.0;
    return unlock_state(&locked, 1);
}

int ageos_scheduler_admit_model_job(
    const char *specialty,
    const char *model_name,
    int niceness,
    double requested_ram_gb,
    double requested_vram_gb,
    int *allowed,
    char *state,
    size_t state_size,
    char *reason,
    size_t reason_size
) {
    if (allowed == NULL || state == NULL || state_size == 0 || reason == NULL || reason_size == 0) {
        return -1;
    }
    ageos_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }

    int existing_index = find_model(&locked.state, model_name);
    if (existing_index >= 0) {
        locked.state.models[existing_index].last_used = ageos_now_seconds();
        *allowed = 1;
        copy_field(state, state_size, ram_state(&locked.state, 0.0));
        copy_field(reason, reason_size, "");
        return unlock_state(&locked, 1);
    }

    int fits_after_eviction = evict_idle_until_fits(&locked.state, model_name, requested_ram_gb, requested_vram_gb);
    const char *current_state = ram_state(&locked.state, requested_ram_gb);
    const char *current_reason = "";
    int is_allowed = fits_after_eviction;
    if (!fits_after_eviction) {
        current_reason = "not enough RAM/VRAM and no idle model can be evicted";
    } else if (strcmp(current_state, "no_ram") == 0 && niceness > 0) {
        is_allowed = 0;
        current_reason = "no RAM: background job queued until memory is freed";
    } else if (strcmp(current_state, "low") == 0 && niceness >= 10) {
        is_allowed = 0;
        current_reason = "RAM low: background job waiting behind higher-priority work";
    } else {
        double vram_total_gb = effective_vram_limit_gb(&locked.state);
        if (
            requested_vram_gb > 0.0 &&
            vram_total_gb > 0.0 &&
            committed_vram_gb(&locked.state) + requested_vram_gb + (vram_total_gb * AGEOS_GPU_RESERVED_VRAM_PERCENT / 100.0) > vram_total_gb &&
            niceness > 0
        ) {
            is_allowed = 0;
            current_reason = "VRAM low: background job queued";
        }
    }

    *allowed = is_allowed;
    copy_field(state, state_size, current_state);
    copy_field(reason, reason_size, current_reason);
    if (!is_allowed) {
        char job_id[AGEOS_FIELD_SMALL];
        snprintf(job_id, sizeof(job_id), "job-%ld-%d", (long)time(NULL), (int)getpid());
        add_queue_item_locked(&locked.state, job_id, "model_load", specialty, model_name, niceness, current_reason);
    }
    return unlock_state(&locked, 1);
}

int ageos_scheduler_register_agent(
    const char *agent_id,
    int64_t pid,
    const char *binary,
    int niceness,
    const char *specialty
) {
    if (agent_id == NULL || agent_id[0] == '\0') {
        return -1;
    }
    ageos_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_agent(&locked.state, agent_id);
    if (index < 0) {
        index = find_free_agent(&locked.state);
    }
    if (index < 0) {
        unlock_state(&locked, 0);
        return -1;
    }

    ageos_agent_record *record = &locked.state.agents[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->agent_id, sizeof(record->agent_id), agent_id);
    record->pid = pid;
    copy_field(record->binary, sizeof(record->binary), binary);
    record->niceness = niceness;
    copy_field(record->status, sizeof(record->status), "running");
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    record->registered_at = ageos_now_seconds();
    return unlock_state(&locked, 1);
}

int ageos_scheduler_deregister_agent(const char *agent_id) {
    ageos_locked_state locked;
    if (agent_id == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_agent(&locked.state, agent_id);
    if (index >= 0) {
        memset(&locked.state.agents[index], 0, sizeof(locked.state.agents[index]));
    }
    return unlock_state(&locked, 1);
}

int ageos_scheduler_mark_model_loaded(
    const char *name,
    const char *specialty,
    const char *backend,
    double ram_gb,
    double vram_gb,
    int64_t pid,
    int port
) {
    if (name == NULL || name[0] == '\0') {
        return -1;
    }
    ageos_locked_state locked;
    if (lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        locked.state.models[index].refcount += 1;
        locked.state.models[index].pid = pid;
        locked.state.models[index].port = port;
        locked.state.models[index].last_used = ageos_now_seconds();
        return unlock_state(&locked, 1);
    }
    index = find_free_model(&locked.state);
    if (index < 0) {
        unlock_state(&locked, 0);
        return -1;
    }
    ageos_model_record *record = &locked.state.models[index];
    memset(record, 0, sizeof(*record));
    record->active = 1;
    copy_field(record->name, sizeof(record->name), name);
    copy_field(record->specialty, sizeof(record->specialty), specialty);
    copy_field(record->backend, sizeof(record->backend), backend);
    record->ram_gb = ram_gb;
    record->vram_gb = vram_gb;
    record->pid = pid;
    record->port = port;
    record->refcount = 1;
    record->loaded_at = ageos_now_seconds();
    record->last_used = record->loaded_at;
    return unlock_state(&locked, 1);
}

int ageos_scheduler_mark_model_unloaded(const char *name) {
    ageos_locked_state locked;
    if (name == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        if (locked.state.models[index].refcount > 0) {
            locked.state.models[index].refcount -= 1;
        }
        locked.state.models[index].last_used = ageos_now_seconds();
    }
    return unlock_state(&locked, 1);
}

int ageos_scheduler_evict_model(const char *name) {
    ageos_locked_state locked;
    if (name == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    int index = find_model(&locked.state, name);
    if (index >= 0) {
        evict_model_at(&locked.state, index);
    }
    return unlock_state(&locked, 1);
}

int ageos_scheduler_add_queue_item(
    const char *job_id,
    const char *kind,
    const char *specialty,
    const char *model_name,
    int niceness,
    const char *reason
) {
    ageos_locked_state locked;
    if (job_id == NULL || lock_state(&locked) != 0) {
        return -1;
    }
    add_queue_item_locked(&locked.state, job_id, kind, specialty, model_name, niceness, reason);
    return unlock_state(&locked, 1);
}

static void json_append(ageos_json_builder *builder, const char *fmt, ...) {
    if (builder->failed || builder->len >= builder->cap) {
        builder->failed = 1;
        return;
    }
    va_list args;
    va_start(args, fmt);
    int written = vsnprintf(builder->data + builder->len, builder->cap - builder->len, fmt, args);
    va_end(args);
    if (written < 0 || (size_t)written >= builder->cap - builder->len) {
        builder->failed = 1;
        return;
    }
    builder->len += (size_t)written;
}

static void json_string(ageos_json_builder *builder, const char *value) {
    json_append(builder, "\"");
    if (value != NULL) {
        for (const unsigned char *p = (const unsigned char *)value; *p != '\0'; p++) {
            if (*p == '"' || *p == '\\') {
                json_append(builder, "\\%c", *p);
            } else if (*p >= 0x20) {
                json_append(builder, "%c", *p);
            }
        }
    }
    json_append(builder, "\"");
}

char *ageos_scheduler_snapshot_json(void) {
    ageos_locked_state locked;
    if (lock_state(&locked) != 0) {
        return NULL;
    }
    char *buffer = malloc(AGEOS_JSON_CAPACITY);
    if (buffer == NULL) {
        unlock_state(&locked, 0);
        return NULL;
    }
    ageos_json_builder json = {
        .data = buffer,
        .len = 0,
        .cap = AGEOS_JSON_CAPACITY,
        .failed = 0,
    };
    const char *current_memory_pressure = ram_state(&locked.state, 0.0);
    json_append(
        &json,
        "{\"hardware\":{\"ram_bytes\":%llu,\"vram_bytes\":%llu},\"limits\":{\"ram_bytes\":%llu,\"vram_bytes\":%llu},\"memory_pressure\":",
        (unsigned long long)ageos_hw_total_ram_bytes(),
        (unsigned long long)ageos_hw_vram_bytes(),
        (unsigned long long)(effective_ram_limit_gb(&locked.state) * 1073741824.0),
        (unsigned long long)(effective_vram_limit_gb(&locked.state) * 1073741824.0)
    );
    json_string(&json, current_memory_pressure);
    json_append(&json, ",\"agents\":[");
    int first = 1;
    for (int i = 0; i < AGEOS_MAX_AGENTS; i++) {
        ageos_agent_record *item = &locked.state.agents[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"agent_id\":", first ? "" : ",");
        json_string(&json, item->agent_id);
        json_append(&json, ",\"pid\":%lld,\"binary\":", (long long)item->pid);
        json_string(&json, item->binary);
        json_append(&json, ",\"niceness\":%d,\"status\":", item->niceness);
        json_string(&json, item->status);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"age_seconds\":%.0f}", ageos_now_seconds() - item->registered_at);
        first = 0;
    }
    json_append(&json, "],\"models\":[");
    first = 1;
    for (int i = 0; i < AGEOS_MAX_MODELS; i++) {
        ageos_model_record *item = &locked.state.models[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"name\":", first ? "" : ",");
        json_string(&json, item->name);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"backend\":");
        json_string(&json, item->backend);
        json_append(
            &json,
            ",\"ram_gb\":%.3f,\"vram_gb\":%.3f,\"pid\":%lld,\"port\":%d,\"refcount\":%d,\"age_seconds\":%.0f,\"idle_seconds\":%.0f}",
            item->ram_gb,
            item->vram_gb,
            (long long)item->pid,
            item->port,
            item->refcount,
            ageos_now_seconds() - item->loaded_at,
            ageos_now_seconds() - item->last_used
        );
        first = 0;
    }
    json_append(&json, "],\"queue\":[");
    first = 1;
    for (int i = 0; i < AGEOS_MAX_QUEUE; i++) {
        ageos_queue_record *item = &locked.state.queue[i];
        if (!item->active) {
            continue;
        }
        json_append(&json, "%s{\"job_id\":", first ? "" : ",");
        json_string(&json, item->job_id);
        json_append(&json, ",\"kind\":");
        json_string(&json, item->kind);
        json_append(&json, ",\"specialty\":");
        json_string(&json, item->specialty);
        json_append(&json, ",\"model_name\":");
        json_string(&json, item->model_name);
        json_append(&json, ",\"niceness\":%d,\"wait_seconds\":%.0f,\"reason\":", item->niceness, ageos_now_seconds() - item->created_at);
        json_string(&json, item->reason);
        json_append(&json, "}");
        first = 0;
    }
    json_append(&json, "]}");
    unlock_state(&locked, 0);
    if (json.failed) {
        free(buffer);
        return NULL;
    }
    return buffer;
}

void ageos_scheduler_free_string(char *value) {
    free(value);
}
