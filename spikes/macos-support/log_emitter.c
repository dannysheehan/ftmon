#include <os/log.h>
#include <stdio.h>

int main(int argc, char **argv) {
    const char *message = argc > 1 ? argv[1] : "FTMON_MACOS_SPIKE";
    os_log_t log = os_log_create("org.ftmon.spike", "validation");
    os_log(log, "%{public}s", message);
    printf("%s\n", message);
    return 0;
}
