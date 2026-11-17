// Deadline-based countdown shared by rate-limit messages. The remaining number is derived
// from a deadline so a throttled background tab cannot drift.
export interface Countdown {
  /** Starts (or restarts) the countdown; returns the clamped whole seconds. */
  start(seconds: number): number;
  stop(): void;
}

export function createCountdown(onTick: (remaining: number) => void, onDone: () => void): Countdown {
  let timer: ReturnType<typeof setTimeout> | undefined;
  let deadline = 0;

  function stop(): void {
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  }

  function tick(): void {
    timer = setTimeout(() => {
      const left = Math.ceil((deadline - Date.now()) / 1000);
      if (left <= 0) {
        timer = undefined;
        onDone();
      } else {
        onTick(left);
        tick();
      }
    }, 1000);
  }

  function start(seconds: number): number {
    stop();
    const n = Math.min(Math.max(Math.ceil(seconds), 1), 3600);
    deadline = Date.now() + n * 1000;
    onTick(n);
    tick();
    return n;
  }

  return { start, stop };
}
