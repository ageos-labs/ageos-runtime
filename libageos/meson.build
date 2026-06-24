project('ageos', 'c', version: '0.1.0', default_options: ['warning_level=2', 'c_std=gnu11'])

cc = meson.get_compiler('c')
seccomp_dep = dependency('libseccomp', required: false)

include_dir = include_directories('include')

libageos_sources = [
  'hw_detect.c',
  'limits.c',
  'landlock.c',
  'log.c',
  'overfs.c',
  'scheduler.c',
  'sandbox.c',
]

libageos = shared_library(
  'ageos',
  libageos_sources,
  include_directories: include_dir,
  dependencies: seccomp_dep,
  install: true,
)

executable(
  'ageos-sandbox',
  ['ageos_sandbox_main.c'],
  include_directories: include_dir,
  link_with: libageos,
  install: true,
)

native_tests = [
  {
    'name': 'test-log',
    'sources': ['tests/test_log.c', 'log.c'],
  },
  {
    'name': 'test-hw-detect',
    'sources': ['tests/test_hw_detect.c', 'hw_detect.c'],
    'link_args': [
      '-Wl,--wrap=fopen',
      '-Wl,--wrap=sysconf',
      '-Wl,--wrap=popen',
      '-Wl,--wrap=pclose',
    ],
  },
]

foreach native_test : native_tests
  exe = executable(
    native_test['name'],
    native_test['sources'],
    include_directories: include_dir,
    link_args: native_test.get('link_args', []),
  )
  test(native_test['name'], exe)
endforeach
