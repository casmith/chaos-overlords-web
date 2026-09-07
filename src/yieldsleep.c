/*
 * yieldsleep -- stop Chaos Overlords from burning a whole CPU core doing
 * nothing.
 *
 * The game's message loop is the 1996 idiom: peek for a message, and if there
 * is none, yield and peek again. Wine turns that yield into NtYieldExecution,
 * which is two getrusage calls around a sched_yield. On an idle Linux container
 * sched_yield returns immediately -- there is nothing else runnable -- so the
 * loop spins as fast as the kernel will let it. Measured with strace on an
 * untouched session: 10,500 iterations a second, 866,000 syscalls in 15
 * seconds, and 103% of a core with the game sitting on its title screen.
 *
 *   47.09%  recvmsg      391248 calls, all EAGAIN   (peeking at the queue)
 *   33.84%  getrusage    315583 calls              \ NtYieldExecution
 *   18.42%  sched_yield  157792 calls              /
 *
 * None of it is work. Six sessions on a four-core VM is six pinned cores.
 *
 * So: intercept sched_yield and sleep instead. A 500 microsecond sleep leaves
 * ~1,400 loop iterations a second, which is still two orders of magnitude
 * faster than anything a player can perceive, and drops the container from
 * 103% of a core to under 5%.
 *
 * Only the main thread is slowed. Wine's audio and wined3d threads have their
 * own reasons to yield and are left to the real syscall -- the whole point is
 * to catch one specific spinning message pump, not to slow the process down.
 *
 * Measured effect (title screen, then a started game, GOG copy, Wine 10):
 *
 *   container CPU        103%  ->  4.4%
 *   click to repaint     min 26ms / med 114ms  ->  min 26ms / med 115ms
 *   intro cinematic      137s  ->  140s   (same length, so the game clock is
 *                                          unchanged; it does render about
 *                                          half as many distinct frames)
 *
 * Set WINE_YIELD_SLEEP_US=0 to disable at run time.
 */
#define _GNU_SOURCE
#include <sched.h>
#include <time.h>
#include <stdlib.h>
#include <unistd.h>
#include <sys/syscall.h>

static long sleep_ns = 500000;   /* 500 us */
static pid_t main_tid;

__attribute__((constructor))
static void yieldsleep_init(void)
{
    const char *e = getenv("WINE_YIELD_SLEEP_US");

    if (e && *e) {
        long v = atol(e);
        sleep_ns = v > 0 ? v * 1000L : 0;
    }
    /* In Linux the thread group leader's tid equals the pid, so this is the
       main thread's id -- captured here, before any thread has been created. */
    main_tid = getpid();
}

int sched_yield(void)
{
    struct timespec ts;

    if (sleep_ns <= 0 || (pid_t)syscall(SYS_gettid) != main_tid)
        return syscall(SYS_sched_yield);

    ts.tv_sec = sleep_ns / 1000000000L;
    ts.tv_nsec = sleep_ns % 1000000000L;
    nanosleep(&ts, NULL);

    /* Wine's NtYieldExecution compares this thread's context-switch counts
       either side of the call and reports STATUS_NO_YIELD_PERFORMED when they
       match. Sleeping guarantees a switch, so it now reports a real yield --
       which is true, and is what a caller waiting for one wants to hear. */
    return 0;
}
