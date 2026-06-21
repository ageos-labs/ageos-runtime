#ifndef AGEOS_LOG_H
#define AGEOS_LOG_H

typedef enum {
    AGEOS_LOG_LEVEL_ERROR = 0,
    AGEOS_LOG_LEVEL_INFO = 1,
    AGEOS_LOG_LEVEL_DEBUG = 2,
} ageos_log_level_t;

void ageos_log_init(void);
void ageos_log_set_level(const char *level);
void ageos_log_set_file(const char *path);
void ageos_log_write(int level, const char *file, int line, const char *text, const char *fmt, ...);

#define AGEOS_LOG_ERROR(text, fmt, ...) \
    ageos_log_write(AGEOS_LOG_LEVEL_ERROR, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)
#define AGEOS_LOG_INFO(text, fmt, ...) \
    ageos_log_write(AGEOS_LOG_LEVEL_INFO, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)
#define AGEOS_LOG_DEBUG(text, fmt, ...) \
    ageos_log_write(AGEOS_LOG_LEVEL_DEBUG, __FILE__, __LINE__, text, fmt, ##__VA_ARGS__)

#endif
