#define _GNU_SOURCE
#include <dlfcn.h>
#include <link.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static int runtime_count = 0;
static int versioned_count = 0;
static int visit(struct dl_phdr_info *info, size_t size, void *data) {
    (void)size; (void)data;
    const char *base = strrchr(info->dlpi_name, '/');
    base = base ? base + 1 : info->dlpi_name;
    if (!strcmp(base, "libpython3.13.so")) runtime_count++;
    if (!strcmp(base, "libpython3.13.so.1.0")) versioned_count++;
    return 0;
}
int main(int argc, char **argv) {
    if (argc < 3 || argc > 5) { fprintf(stderr, "usage: dlopen-smoke PREFIX CONSUMER_DSO [PYTHON_DSO [NATIVE_DIR]]\n"); return 2; }
    if (dlsym(RTLD_DEFAULT, "Py_IsInitialized")) return 3;
    char path[4096];
    if (snprintf(path, sizeof(path), "%s/lib/libpython3.13.so", argv[1]) >= (int)sizeof(path)) return 4;
    void *python = dlopen(argc >= 4 ? argv[3] : path, RTLD_NOW | RTLD_GLOBAL);
    if (!python) { fprintf(stderr, "dlopen python: %s\n", dlerror()); return 5; }
    /* Fault injection for the audit's duplicate-runtime rejection test only. */
    const char *second = getenv("HAP_PROBE_SECOND_LIBRARY");
    if (second && !dlopen(second, RTLD_NOW | RTLD_LOCAL)) {
        fprintf(stderr, "dlopen second fixture: %s\n", dlerror()); return 10;
    }
    void *consumer = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
    if (!consumer) { fprintf(stderr, "dlopen consumer: %s\n", dlerror()); return 6; }
    void *(*address)(void) = (void *(*)(void))dlsym(consumer, "hap_runtime_address");
    int (*probe)(const char *) = (int (*)(const char *))dlsym(consumer, "hap_python_probe");
    void *is_initialized = dlsym(python, "Py_IsInitialized");
    void *runtime = dlsym(python, "_PyRuntime");
    if (!address || !probe || !is_initialized || !runtime) return 7;
    if (address() != is_initialized || dlsym(consumer, "Py_IsInitialized") != is_initialized ||
        dlsym(consumer, "_PyRuntime") != runtime || dlsym(RTLD_DEFAULT, "_PyRuntime") != runtime) return 8;
    dl_iterate_phdr(visit, NULL);
    if (runtime_count != 1 || versioned_count) {
        fprintf(stderr, "runtime count=%d versioned=%d\n", runtime_count, versioned_count); return 9;
    }
    int result;
    if (argc == 5) {
        int (*native_probe)(const char *, const char *) = (int (*)(const char *, const char *))dlsym(consumer, "hap_python_probe_with_native_dir");
        if (!native_probe) return 7;
        result = native_probe(argv[1], argv[4]);
    } else {
        result = probe(argv[1]);
    }
    if (result) return result;
    runtime_count = 0; versioned_count = 0;
    dl_iterate_phdr(visit, NULL);
    if (runtime_count != 1 || versioned_count || dlsym(RTLD_DEFAULT, "_PyRuntime") != runtime ||
        dlsym(consumer, "_PyRuntime") != runtime || address() != is_initialized) {
        fprintf(stderr, "post-import runtime count=%d versioned=%d\n", runtime_count, versioned_count); return 9;
    }
    printf("Unique shared PyRuntime: one libpython3.13.so before/after imports, global/consumer symbol addresses match\n");
    /* CPython3.13 may keep internal TLS references; process exit owns unloading. */
    return 0;
}
