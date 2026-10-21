#!/usr/bin/env node
/**
 * Vite 启动器。
 *
 * 在受限的 Windows 沙箱中，Vite 启动时会执行 `net use` 探测网络盘，
 * 而沙箱禁止 Node 用管道创建子进程，导致 `spawn EPERM` 并让 npm run build 失败。
 * 本项目始终位于本地磁盘，不需要网络盘映射，因此只跳过这一条探测命令，
 * 其余子进程调用保持不变。正常运行环境下该行为不会影响构建结果。
 */

const childProcess = require('node:child_process')
const path = require('node:path')
const { pathToFileURL } = require('node:url')

const originalExec = childProcess.exec
childProcess.exec = function patchedExec(command, options, callback) {
  if (typeof command === 'string' && command.trim().toLowerCase() === 'net use') {
    const done = typeof options === 'function' ? options : callback
    if (typeof done === 'function') {
      process.nextTick(() => {
        const error = new Error('跳过网络盘探测（本地项目不需要）')
        error.code = 'ENOENT'
        done(error)
      })
    }
    return {
      on() {
        return this
      },
      once() {
        return this
      },
      kill() {},
      unref() {},
      stdout: null,
      stderr: null,
      pid: -1
    }
  }
  return originalExec.apply(this, arguments)
}

const vitePackageJson = require.resolve('vite/package.json')
const viteBin = path.join(path.dirname(vitePackageJson), 'bin', 'vite.js')

import(pathToFileURL(viteBin).href).catch((error) => {
  console.error(error)
  process.exitCode = 1
})
