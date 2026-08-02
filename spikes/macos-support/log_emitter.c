#include <os/log.h>
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    const char *message = argc > 1 ? argv[1] : "FTMON_MACOS_SPIKE";
    int count = argc > 2 ? atoi(argv[2]) : 1;
    os_log_t log = os_log_create("org.ftmon.spike", "validation");
    for (int i = 0; i < count; i++) {
        os_log_fault(log, "%{public}s", message);
    }
    printf("%s (%d events)\n", message, count);
    return 0;
}
