// Author: heimoxian
// Navigation policy only; persistence and model calls belong to the caller.
export function createCompanionScheduler({ run, publish, delay = 1200,
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  let enabled = false
  let generation = 0
  let timer = null
  let active = null
  let target = null
  let pending = null
  let ready = false
  let jump = false

  function cancelTimer() {
    if (timer !== null) clearTimer(timer)
    timer = null
    ready = false
  }

  async function drain() {
    if (!enabled || active || !ready || jump || !target) return
    ready = false
    const request = { ...target, generation, controller: new AbortController() }
    active = request
    publish({ status: 'running', ...target })
    try {
      const result = await run({ ...target, signal: request.controller.signal })
      if (enabled && generation === request.generation) {
        publish({ status: 'completed', ...target, result })
      }
    } catch (error) {
      if (enabled && generation === request.generation) {
        publish({ status: 'failed', ...target, error })
      }
    } finally {
      active = null
      void drain()
    }
  }

  function schedule(next) {
    cancelTimer()
    generation += 1
    target = next
    active?.controller.abort()
    publish({ status: 'waiting', ...target })
    timer = setTimer(() => {
      timer = null
      ready = true
      void drain()
    }, delay)
  }

  function stop() {
    enabled = false
    generation += 1
    cancelTimer()
    pending = null
    jump = false
    active?.controller.abort()
    publish({ status: 'paused' })
  }

  return {
    start(bookId, chapterIdx) {
      enabled = true
      pending = null
      jump = false
      schedule({ bookId, chapterIdx })
    },
    navigate(bookId, chapterIdx) {
      if (!enabled) return
      const next = { bookId, chapterIdx }
      if (target?.bookId !== bookId) return stop()
      if (!jump && target?.chapterIdx === chapterIdx) return
      const previous = pending || target
      if (jump || Math.abs(chapterIdx - previous.chapterIdx) > 1) {
        cancelTimer()
        generation += 1
        active?.controller.abort()
        pending = next
        jump = true
        publish({ status: 'jump', ...next })
        return
      }
      schedule(next)
    },
    acceptJump() {
      if (!enabled || !pending) return
      const next = pending
      pending = null
      jump = false
      schedule(next)
    },
    retry() {
      if (enabled && target && !jump) schedule(target)
    },
    stop,
    dispose: stop,
  }
}
