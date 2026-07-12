use zeroize::Zeroizing;

#[cfg(windows)]
mod platform {
    use super::*;
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, ERROR_NOT_FOUND, WAIT_ABANDONED, WAIT_OBJECT_0,
    };
    use windows_sys::Win32::Security::Credentials::{
        CredDeleteW, CredFree, CredReadW, CredWriteW, CREDENTIALW, CRED_MAX_CREDENTIAL_BLOB_SIZE,
        CRED_PERSIST_LOCAL_MACHINE, CRED_TYPE_GENERIC,
    };
    use windows_sys::Win32::System::Threading::{CreateMutexW, ReleaseMutex, WaitForSingleObject};

    const INSTALLATION_LOCK_TIMEOUT_MS: u32 = 10_000;

    struct InstallationLock(*mut core::ffi::c_void);

    impl Drop for InstallationLock {
        fn drop(&mut self) {
            unsafe {
                ReleaseMutex(self.0);
                CloseHandle(self.0);
            }
        }
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn write(target: &str, secret: &[u8]) -> Result<(), String> {
        if secret.is_empty() || secret.len() > CRED_MAX_CREDENTIAL_BLOB_SIZE as usize {
            return Err("native_credential_invalid".to_string());
        }
        let mut target = wide(target);
        let mut username = wide("home-agent-native-oauth");
        let mut blob = Zeroizing::new(secret.to_vec());
        let credential = CREDENTIALW {
            Type: CRED_TYPE_GENERIC,
            TargetName: target.as_mut_ptr(),
            CredentialBlobSize: blob.len() as u32,
            CredentialBlob: blob.as_mut_ptr(),
            Persist: CRED_PERSIST_LOCAL_MACHINE,
            UserName: username.as_mut_ptr(),
            ..Default::default()
        };
        let result = unsafe { CredWriteW(&credential, 0) };
        target.fill(0);
        username.fill(0);
        if result == 0 {
            return Err(format!("native_credential_write_failed:{}", unsafe {
                GetLastError()
            }));
        }
        Ok(())
    }

    pub fn read(target: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
        let mut target = wide(target);
        let mut credential: *mut CREDENTIALW = null_mut();
        let result = unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut credential) };
        target.fill(0);
        if result == 0 {
            let error = unsafe { GetLastError() };
            return if error == ERROR_NOT_FOUND {
                Ok(None)
            } else {
                Err(format!("native_credential_read_failed:{error}"))
            };
        }
        if credential.is_null() {
            return Err("native_credential_read_failed".to_string());
        }
        let output = unsafe {
            let item = &mut *credential;
            let size = item.CredentialBlobSize as usize;
            if size == 0 || size > CRED_MAX_CREDENTIAL_BLOB_SIZE as usize {
                CredFree(credential.cast());
                return Err("native_credential_invalid".to_string());
            }
            let bytes = std::slice::from_raw_parts_mut(item.CredentialBlob, size);
            let copy = Zeroizing::new(bytes.to_vec());
            bytes.fill(0);
            CredFree(credential.cast());
            copy
        };
        Ok(Some(output))
    }

    pub fn delete(target: &str) -> Result<(), String> {
        let mut target = wide(target);
        let result = unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) };
        target.fill(0);
        if result == 0 {
            let error = unsafe { GetLastError() };
            if error != ERROR_NOT_FOUND {
                return Err(format!("native_credential_delete_failed:{error}"));
            }
        }
        Ok(())
    }

    pub fn exists(target: &str) -> Result<bool, String> {
        Ok(read(target)?.is_some())
    }

    pub fn with_installation_lock<T>(
        operation: impl FnOnce() -> Result<T, String>,
    ) -> Result<T, String> {
        let mut name = wide("Global\\EngineeredLighting.HomeAgent.InstallationAttestation.v1");
        let handle = unsafe { CreateMutexW(null(), 0, name.as_ptr()) };
        name.fill(0);
        if handle.is_null() {
            return Err(format!("native_attestation_lock_failed:{}", unsafe {
                GetLastError()
            }));
        }
        let acquired = unsafe { WaitForSingleObject(handle, INSTALLATION_LOCK_TIMEOUT_MS) };
        if acquired != WAIT_OBJECT_0 && acquired != WAIT_ABANDONED {
            unsafe { CloseHandle(handle) };
            return Err("native_attestation_lock_failed".to_string());
        }
        let _guard = InstallationLock(handle);
        operation()
    }

    pub fn open_external(url: &str) -> Result<(), String> {
        use windows_sys::Win32::UI::Shell::ShellExecuteW;
        use windows_sys::Win32::UI::WindowsAndMessaging::SW_SHOWNORMAL;

        let mut operation = wide("open");
        let mut target = wide(url);
        let result = unsafe {
            ShellExecuteW(
                null_mut(),
                operation.as_ptr(),
                target.as_ptr(),
                null(),
                null(),
                SW_SHOWNORMAL,
            )
        };
        operation.fill(0);
        target.fill(0);
        if result as isize <= 32 {
            return Err("native_browser_open_failed".to_string());
        }
        Ok(())
    }
}

#[cfg(not(windows))]
mod platform {
    use super::*;

    pub fn write(_target: &str, _secret: &[u8]) -> Result<(), String> {
        Err("native_auth_unsupported".to_string())
    }
    pub fn read(_target: &str) -> Result<Option<Zeroizing<Vec<u8>>>, String> {
        Err("native_auth_unsupported".to_string())
    }
    pub fn delete(_target: &str) -> Result<(), String> {
        Err("native_auth_unsupported".to_string())
    }
    pub fn exists(_target: &str) -> Result<bool, String> {
        Ok(false)
    }
    pub fn with_installation_lock<T>(
        _operation: impl FnOnce() -> Result<T, String>,
    ) -> Result<T, String> {
        Err("native_auth_unsupported".to_string())
    }
    pub fn open_external(_url: &str) -> Result<(), String> {
        Err("native_auth_unsupported".to_string())
    }
}

pub use platform::{delete, exists, open_external, read, with_installation_lock, write};
