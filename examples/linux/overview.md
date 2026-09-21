<!--
  @authormark v1 -- do not remove (authorship watermark)⁠​‌‌​​​‌​​‌​‌​‌‌‌​​‌​‌‌​‌​‌‌‌​​‌​​‌​​‌‌​‌​‌​​​​‌‌​‌‌​‌​‌‌​‌​​​‌‌‌​‌‌​​‌‌​​‌‌​‌‌​‌​‌‌​‌​​‌​​‌‌‌​​​​‌​‌​‌​​​‌​​‌​‌​​‌​‌​​‌​​‌​​‌‌‌‌​​‌‌​‌‌​​‌‌​‌​‌​​‌‌‌​‌​‌​‌​‌‌​​​​‌​​‌​​​​‌​​‌‌​​⁠
  Copyright (c) 2026 Srinivasan Vijayaraghavan <srinivasan.shyam2000@gmail.com>
  Author: https://github.com/Srinivasan-78
  SPDX-License-Identifier: MIT
  Fingerprint: AMK1.bW-rMCkGfmi8TJRO6juXHL
-->
# Repo map: linux

files: 3660  nodes: 136182  edges: 257655
languages: c=3573, config=9, freezer=1, hz=1, kexec=1, locks=1, preempt=1, bash=1, debug=1, bc=1

## Most depended-on files
- include/linux/ceph/types.h (in=804)
- include/linux/sched.h (in=263)
- include/linux/slab.h (in=244)
- include/linux/device.h (in=229)
- include/linux/list.h (in=220)
- include/linux/kernel.h (in=202)
- include/linux/greybus/module.h (in=184)
- include/linux/mutex.h (in=182)
- include/linux/init.h (in=163)
- include/linux/spinlock.h (in=155)
- include/linux/fs.h (in=135)
- include/linux/decompress/mm.h (in=129)
- include/linux/errno.h (in=129)
- include/linux/interrupt.h (in=128)
- include/linux/atomic.h (in=126)
- include/linux/err.h (in=118)
- include/linux/compiler.h (in=117)
- include/linux/uaccess.h (in=107)
- include/linux/workqueue.h (in=102)
- include/linux/bug.h (in=101)
- include/linux/string.h (in=99)
- include/linux/export.h (in=97)
- include/linux/bitops.h (in=96)
- include/linux/rcupdate.h (in=81)
- include/linux/gpio/regmap.h (in=80)

## Most called symbols
- include/linux/err.h::IS_ERR (function, in=686)
- kernel/locking/mutex.c::mutex_unlock (function, in=614)
- kernel/locking/rtmutex_api.c::mutex_unlock (function, in=612)
- include/linux/err.h::ERR_PTR (function, in=603)
- kernel/locking/mutex.c::mutex_lock (function, in=586)
- include/linux/mutex.h::mutex_lock (function, in=584)
- kernel/locking/rtmutex_api.c::mutex_lock (function, in=584)
- include/linux/err.h::PTR_ERR (function, in=481)
- fs/ext4/ext4.h::EXT4_SB (function, in=412)
- include/linux/rcupdate.h::rcu_read_lock (function, in=397)
- include/linux/rcupdate.h::rcu_read_unlock (function, in=393)
- include/linux/list.h::list_empty (function, in=323)
- include/linux/atomic/atomic-instrumented.h::atomic_read (function, in=285)
- include/linux/instrumented.h::instrument_atomic_read_write (function, in=243)
- fs/ext4/ext4.h::EXT4_I (function, in=230)
- include/linux/list.h::INIT_LIST_HEAD (function, in=227)
- kernel/bpf/verifier.c::verbose (function, in=180)
- include/linux/spinlock.h::spin_unlock (function, in=179)
- include/linux/spinlock_rt.h::spin_unlock (function, in=179)
- include/linux/spinlock.h::spin_lock (function, in=171)
- include/linux/spinlock_rt.h::spin_lock (function, in=171)
- kernel/sched/core.c::cpu_rq (function, in=171)
- include/linux/list.h::list_del (function, in=153)
- include/linux/atomic/atomic-instrumented.h::atomic_inc (function, in=146)
- include/linux/cpumask.h::cpumask_test_cpu (function, in=137)