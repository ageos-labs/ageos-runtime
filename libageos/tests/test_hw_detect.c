#define _GNU_SOURCE

#include "ageos/hw.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int g_meminfo_mode = 0;
static long g_pages = 0;
static long g_page_size = 0;
static const char *g_popen_output = NULL;
static int g_popen_fail = 0;

static FILE *open_buffered_stream(const char *contents) {
    FILE *fp = tmpfile();
    if (fp == NULL) {
        return NULL;
    }
    if (contents != NULL) {
        fputs(contents, fp);
        rewind(fp);
    }
    return fp;
}

FILE *__wrap_fopen(const char *path, const char *mode) {
    (void)mode;
    if (strcmp(path, "/proc/meminfo") != 0) {
        return NULL;
    }
    if (g_meminfo_mode == 0) {
        return open_buffered_stream("MemTotal: 2048 kB\n");
    }
    if (g_meminfo_mode == 1) {
        return open_buffered_stream("SwapTotal: 16 kB\n");
    }
    return NULL;
}

long __wrap_sysconf(int name) {
    if (name == _SC_PHYS_PAGES) {
        return g_pages;
    }
    if (name == _SC_PAGE_SIZE) {
        return g_page_size;
    }
    return -1;
}

FILE *__wrap_popen(const char *command, const char *mode) {
    (void)command;
    (void)mode;
    if (g_popen_fail) {
        return NULL;
    }
    return open_buffered_stream(g_popen_output);
}

int __wrap_pclose(FILE *fp) {
    return fclose(fp);
}

static int expect_u64(const char *label, uint64_t actual, uint64_t expected) {
    if (actual == expected) {
        return 0;
    }
    fprintf(stderr, "%s: expected %llu, got %llu\n", label, (unsigned long long)expected, (unsigned long long)actual);
    return 1;
}

int main(void) {
    int failures = 0;

    g_meminfo_mode = 0;
    failures += expect_u64("meminfo total ram", ageos_hw_total_ram_bytes(), 2048ULL * 1024ULL);

    g_meminfo_mode = 1;
    g_pages = 32;
    g_page_size = 4096;
    failures += expect_u64("fallback total ram", ageos_hw_total_ram_bytes(), 32ULL * 4096ULL);

    g_meminfo_mode = 2;
    g_pages = 0;
    g_page_size = 4096;
    failures += expect_u64("invalid sysconf ram", ageos_hw_total_ram_bytes(), 0);

    g_popen_output = "32\n64\n";
    g_popen_fail = 0;
    failures += expect_u64("max total vram", ageos_hw_vram_bytes(), 64ULL * 1024ULL * 1024ULL);

    g_popen_output = "24\n12\n";
    failures += expect_u64("max free vram", ageos_hw_free_vram_bytes(), 24ULL * 1024ULL * 1024ULL);

    g_popen_fail = 1;
    failures += expect_u64("missing total vram", ageos_hw_vram_bytes(), 0);
    failures += expect_u64("missing free vram", ageos_hw_free_vram_bytes(), 0);

    return failures == 0 ? 0 : 1;
}
