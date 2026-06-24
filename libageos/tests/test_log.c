#define _GNU_SOURCE

#include "ageos/log.h"

#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int read_file(const char *path, char *buffer, size_t size) {
    FILE *fp = fopen(path, "r");
    size_t read;
    if (fp == NULL) {
        return -1;
    }
    read = fread(buffer, 1, size - 1, fp);
    fclose(fp);
    buffer[read] = '\0';
    return 0;
}

static int create_temp_path(char *path_template) {
    int fd = mkstemp(path_template);
    if (fd < 0) {
        return -1;
    }
    close(fd);
    unlink(path_template);
    return 0;
}

static int expect_contains(const char *label, const char *haystack, const char *needle) {
    if (strstr(haystack, needle) != NULL) {
        return 0;
    }
    fprintf(stderr, "%s: expected to find '%s' in '%s'\n", label, needle, haystack);
    return 1;
}

int main(void) {
    int failures = 0;
    char path1[] = "/tmp/ageos-log-debug-XXXXXX";
    char path2[] = "/tmp/ageos-log-info-XXXXXX";
    char allowed_dir[] = "/tmp/ageos-log-home-XXXXXX";
    char allowed_path[512];
    char blocked_path[] = "/tmp/ageos-log-blocked.log";
    char buffer[1024];

    if (create_temp_path(path1) != 0 || create_temp_path(path2) != 0 || mkdtemp(allowed_dir) == NULL) {
        perror("tmp");
        return 1;
    }
    snprintf(allowed_path, sizeof(allowed_path), "%s/agent.log", allowed_dir);
    unlink(blocked_path);

    unsetenv("AGEOS_SANDBOX");
    unsetenv("AGEOS_LOG_LEVEL");

    ageos_log_set_level("debug");
    ageos_log_set_file(path1);
    ageos_log_write(AGEOS_LOG_LEVEL_DEBUG, "/tmp/src/test_log.c", 42, "debug-text", "%s", "value");
    ageos_log_set_file(NULL);
    if (read_file(path1, buffer, sizeof(buffer)) != 0) {
        perror("read path1");
        return 1;
    }
    failures += expect_contains("debug line", buffer, "DEBUG test_log.c:42 debug-text:value");

    setenv("AGEOS_LOG_LEVEL", "info", 1);
    ageos_log_set_file(path2);
    ageos_log_write(AGEOS_LOG_LEVEL_INFO, "/tmp/src/test_log.c", 7, "info-text", "");
    ageos_log_write(AGEOS_LOG_LEVEL_DEBUG, "/tmp/src/test_log.c", 8, "hidden-debug", "");
    ageos_log_set_file(NULL);
    if (read_file(path2, buffer, sizeof(buffer)) != 0) {
        perror("read path2");
        return 1;
    }
    failures += expect_contains("info line", buffer, "INFO test_log.c:7 info-text");
    if (strstr(buffer, "hidden-debug") != NULL) {
        fprintf(stderr, "debug line unexpectedly emitted at info level\n");
        failures++;
    }

    setenv("AGEOS_SANDBOX", "1", 1);
    setenv("HOME", allowed_dir, 1);
    setenv("TMPDIR", allowed_dir, 1);
    ageos_log_set_file(blocked_path);
    ageos_log_write(AGEOS_LOG_LEVEL_ERROR, "/tmp/src/test_log.c", 9, "blocked", "");
    if (access(blocked_path, F_OK) == 0) {
        fprintf(stderr, "blocked sandbox log file was created\n");
        failures++;
    }

    ageos_log_set_file(allowed_path);
    ageos_log_set_level("info");
    ageos_log_write(AGEOS_LOG_LEVEL_ERROR, "/tmp/src/test_log.c", 10, "allowed", "");
    ageos_log_set_file(NULL);
    if (read_file(allowed_path, buffer, sizeof(buffer)) != 0) {
        perror("read allowed_path");
        return 1;
    }
    failures += expect_contains("allowed line", buffer, "ERROR test_log.c:10 allowed");

    unlink(path1);
    unlink(path2);
    unlink(allowed_path);
    rmdir(allowed_dir);
    return failures == 0 ? 0 : 1;
}
