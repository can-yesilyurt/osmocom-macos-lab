/* SPDX-License-Identifier: MIT
 * Copyright (c) 2026 Can Yesilyurt
 *
 * Functional Darwin timerfd/event-loop check for shared CI VMs. This checks
 * event delivery, readable expiration counts and disarm/reset behavior. It
 * deliberately makes no scheduling-latency claim. The pinned port's original
 * strict wall-clock tests remain the default physical-host checks.
 */
#include <osmocom/core/application.h>
#include <osmocom/core/logging.h>
#include <osmocom/core/select.h>
#include <osmocom/core/talloc.h>
#include <sys/timerfd.h>
#include <sys/select.h>
#include <unistd.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>

static struct log_info_cat categories[] = {
    { .name = "DTEST", .enabled = 1, .loglevel = LOGL_NOTICE }
};
static const struct log_info logging = { .cat = categories, .num_cat = 1 };
static struct osmo_fd timer = { .fd = -1 };
static uint64_t expirations;
static unsigned callbacks;
static int failed;
static int disarmed;

static double now(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1000000000.0;
}

static int on_timer(struct osmo_fd *fd, unsigned int what)
{
    uint64_t count = 0;
    ssize_t bytes = read(fd->fd, &count, sizeof(count));
    if (!(what & OSMO_FD_READ) || bytes != sizeof(count) || count == 0) {
        fprintf(stderr, "Invalid timer event: what=%u bytes=%zd count=%llu\n",
                what, bytes, (unsigned long long)count);
        failed = 1;
        return -1;
    }
    callbacks++;
    expirations += count;
    if (expirations >= 6) {
        /* Exercise the same callback-side disarm operation used by daemons. */
        if (osmo_timerfd_disable(fd) != 0)
            failed = 1;
        disarmed = 1;
    }
    return 0;
}

int main(void)
{
    void *ctx = talloc_named_const(NULL, 0, "functional timer test");
    struct timespec first = { .tv_nsec = 1000000 };
    struct timespec interval = { .tv_nsec = 100000000 };
    struct itimerspec current;
    uint64_t pending;
    fd_set readfds;
    struct timeval zero = { 0, 0 };
    double deadline = now() + 10.0;

    if (!ctx || osmo_init_logging2(ctx, &logging) != 0)
        return 1;
    if (osmo_timerfd_setup(&timer, on_timer, NULL) != 0 ||
        osmo_timerfd_schedule(&timer, &first, &interval) != 0 ||
        osmo_timerfd_schedule(&timer, &first, &interval) != 0) {
        fprintf(stderr, "Timer setup/schedule failed\n");
        return 1;
    }
    while (!disarmed && !failed && now() < deadline) {
        osmo_select_main(1);
        usleep(1000);
    }
    if (!disarmed || failed || expirations < 6 || callbacks == 0) {
        fprintf(stderr, "Timer delivery failed: callbacks=%u expirations=%llu disarmed=%d\n",
                callbacks, (unsigned long long)expirations, disarmed);
        return 1;
    }
    if (timerfd_gettime(timer.fd, &current) != 0 ||
        current.it_value.tv_sec != 0 || current.it_value.tv_nsec != 0 ||
        current.it_interval.tv_sec != 0 || current.it_interval.tv_nsec != 0) {
        fprintf(stderr, "Timer remained armed after disable\n");
        return 1;
    }
    if (read(timer.fd, &pending, sizeof(pending)) != -1 || errno != EAGAIN) {
        fprintf(stderr, "Pending expiration survived disarm\n");
        return 1;
    }
    FD_ZERO(&readfds);
    FD_SET(timer.fd, &readfds);
    if (select(timer.fd + 1, &readfds, NULL, NULL, &zero) != 0) {
        fprintf(stderr, "Disarmed timer remained readable\n");
        return 1;
    }
    osmo_fd_unregister(&timer);
    close(timer.fd);
    printf("PASS: callbacks=%u expirations=%llu; disarm/reset verified; latency not assessed\n",
           callbacks, (unsigned long long)expirations);
    return 0;
}
