#define _GNU_SOURCE

#include <errno.h>
#include <linux/landlock.h>
#include <stdio.h>
#include <string.h>
#include <sys/syscall.h>
#include <unistd.h>

#ifndef __NR_landlock_create_ruleset
#error "landlock_create_ruleset syscall number is unavailable"
#endif

#ifndef LANDLOCK_CREATE_RULESET_VERSION
#define LANDLOCK_CREATE_RULESET_VERSION (1U << 0)
#endif

int main(void) {
    errno = 0;
    const long abi = syscall(__NR_landlock_create_ruleset, NULL, 0,
                             LANDLOCK_CREATE_RULESET_VERSION);
    if (abi < 0) {
        fprintf(stderr, "landlock_create_ruleset: %s\n", strerror(errno));
        return 1;
    }
    if (abi == 0) {
        fputs("landlock_create_ruleset returned ABI 0\n", stderr);
        return 1;
    }

    printf("%ld\n", abi);
    return 0;
}
