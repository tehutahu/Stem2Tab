# Shared GPU lock operation

When API servers have separate data roots, set `STEM2TAB_GPU_LOCK_PATH` on every participating API process to the same existing, regular, absolute canonical file. The administrator must create this file before startup. A new server may point to an existing server's `execution.lock` to share that server's default lock. Without the variable, the existing data-root `execution.lock` remains the default.

Use a trusted administrator-managed file and parent directories. Limit who can change them. While any participating server or worker runs, do not rename, unlink, recreate, or replace the lock file or its parents. Deployments and cleanup jobs must preserve the inode. The acquisition checks compare the validated inode with the opened file and recheck the path after locking. These checks detect changes observed during acquisition; they cannot prevent arbitrary replacement after the final check, including replacement by another process with the same UID.

An invalid or unavailable configured lock returns 503 `gpu_lock_unavailable`, with no fallback to a different lock. Contention returns 409 `trial_busy`. Every GPU execution path must honor the same lock; this advisory lock does not control unrelated GPU applications.

CPU tests use Linux temporary files and `flock`. Before deployment, verify effective `flock` exclusion on the actual filesystem between independent participating processes in both directions, while a child retains an inherited descriptor, and after that child exits. WSL/DrvFS and other filesystems have not been validated by these Linux CPU tests.
