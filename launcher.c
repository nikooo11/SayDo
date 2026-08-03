/* Native SayDo launcher: embeds Python so the process IS SayDo.app.
 *
 * A shell-script launcher exec's the python binary, so macOS attributes the
 * process (Dock label, TCC permissions, mic-use panel) to "Python". Running
 * the interpreter in-process from a Mach-O inside the bundle makes macOS see
 * SayDo.app itself. Built by make-app.sh, which fills in the paths.
 */
#include <dlfcn.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef SAYDO_DIR
#define SAYDO_DIR "/Applications/SayDo"
#endif
#ifndef PYTHON_LIB
#define PYTHON_LIB "/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13/Python"
#endif

typedef int (*py_bytes_main)(int, char **);

int main(int argc, char **argv) {
    chdir(SAYDO_DIR);
    setenv("PYTHONPATH", SAYDO_DIR "/.venv/lib/python3.13/site-packages", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);

    /* Python re-invokes this executable for helper children (multiprocessing
     * resource trackers etc). Those calls carry args and MUST be forwarded to
     * the interpreter verbatim — running main.py instead forks a new app per
     * helper, forever. Finder's legacy -psn_* arg is not a child call. */
    int child = argc > 1 && strncmp(argv[1], "-psn", 4) != 0;

    const char *home = getenv("HOME");
    if (!child && home) {
        char log[PATH_MAX];
        snprintf(log, sizeof log, "%s/Library/Logs/saydo.log", home);
        freopen(log, "a", stdout);
        freopen(log, "a", stderr);
        setvbuf(stdout, NULL, _IONBF, 0);
        setvbuf(stderr, NULL, _IONBF, 0);
    }

    void *lib = dlopen(PYTHON_LIB, RTLD_NOW | RTLD_GLOBAL);
    if (!lib) {
        fprintf(stderr, "SayDo launcher: cannot load %s: %s\n", PYTHON_LIB, dlerror());
        return 1;
    }
    py_bytes_main run = (py_bytes_main)dlsym(lib, "Py_BytesMain");
    if (!run) {
        fprintf(stderr, "SayDo launcher: Py_BytesMain missing\n");
        return 1;
    }

    char self[PATH_MAX];
    uint32_t n = sizeof self;
    if (_NSGetExecutablePath(self, &n) != 0)
        strlcpy(self, "SayDo", sizeof self);

    char **pyargv = malloc(sizeof(char *) * (argc + 2));
    int pn = 0;
    pyargv[pn++] = self;
    if (child)
        for (int i = 1; i < argc; i++)
            pyargv[pn++] = argv[i];
    else
        pyargv[pn++] = (char *)SAYDO_DIR "/main.py";
    pyargv[pn] = NULL;
    return run(pn, pyargv);
}
