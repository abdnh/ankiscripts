#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shlobj.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <tlhelp32.h>

// Check if a process with given PID exists
BOOL is_process_running(DWORD pid) {
  HANDLE hProcess = OpenProcess(PROCESS_QUERY_INFORMATION, FALSE, pid);
  if (hProcess == NULL) {
    return FALSE;
  }

  DWORD exitCode;
  BOOL result = GetExitCodeProcess(hProcess, &exitCode);
  CloseHandle(hProcess);

  return result && exitCode == STILL_ACTIVE;
}

// Send file/folder to recycle bin
BOOL send_to_trash(const char *path) {
  // Convert to wide string for Windows API
  int len = MultiByteToWideChar(CP_UTF8, 0, path, -1, NULL, 0);
  if (len == 0)
    return FALSE;

  wchar_t *wpath = malloc(len * sizeof(wchar_t));
  if (!wpath)
    return FALSE;

  MultiByteToWideChar(CP_UTF8, 0, path, -1, wpath, len);

  // Use SHFileOperation to send to recycle bin
  SHFILEOPSTRUCTW fileOp = {0};
  fileOp.wFunc = FO_DELETE;
  fileOp.pFrom = wpath;
  fileOp.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT;

  int result = SHFileOperationW(&fileOp);
  free(wpath);

  if (result != 0) {
    // If recycle bin fails, try to delete permanently
    printf("Recycle bin failed, attempting permanent deletion...\n");
    if (DeleteFileA(path)) {
      return TRUE;
    }
    if (RemoveDirectoryA(path)) {
      return TRUE;
    }
    return FALSE;
  }

  return TRUE;
}

// Check if string ends with suffix
BOOL ends_with(const char *str, const char *suffix) {
  if (!str || !suffix)
    return FALSE;

  size_t str_len = strlen(str);
  size_t suffix_len = strlen(suffix);

  if (suffix_len > str_len)
    return FALSE;

  return strcmp(str + str_len - suffix_len, suffix) == 0;
}

// Launch Anki
BOOL launch_anki(const char *anki_exe, const char *anki_base,
                 const char *package_file) {
  char command[2048];

  if (package_file && strlen(package_file) > 0) {
    snprintf(command, sizeof(command),
             "cmd /c start /B \"\" \"%s\" -b \"%s\" \"%s\"", anki_exe,
             anki_base, package_file);
  } else {
    snprintf(command, sizeof(command), "cmd /c start /B \"\" \"%s\" -b \"%s\"",
             anki_exe, anki_base);
  }

  printf("Executing: %s\n", command);

  STARTUPINFOA si = {0};
  PROCESS_INFORMATION pi = {0};
  si.cb = sizeof(si);

  BOOL result = CreateProcessA(NULL, command, NULL, NULL, FALSE,
                               CREATE_NO_WINDOW, NULL, NULL, &si, &pi);

  if (result) {
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return TRUE;
  }

  return FALSE;
}

void print_usage(const char *program_name) {
  printf("Usage: %s <pid> <anki_exe> <anki_base> <addon_dir_or_package>\n",
         program_name);
  printf("\n");
  printf("Arguments:\n");
  printf("  pid                    Process ID to wait for\n");
  printf("  anki_exe              Path to Anki executable\n");
  printf("  anki_base             Anki base data directory\n");
  printf("  addon_dir_or_package  Add-on directory to delete or package file "
         "to install\n");
}

int main(int argc, char *argv[]) {
  if (argc != 5) {
    print_usage(argv[0]);
    return 1;
  }

  DWORD pid = (DWORD)atol(argv[1]);
  const char *anki_exe = argv[2];
  const char *anki_base = argv[3];
  const char *addon_dir_or_package = argv[4];

  if (pid == 0) {
    printf("Error: Invalid PID\n");
    return 1;
  }

  printf("Waiting for PID %lu to exit...\n", pid);

  // Wait for process to exit
  while (is_process_running(pid)) {
    printf("PID %lu still running, sleeping...\n", pid);
    Sleep(500); // Sleep for 500ms
  }

  printf("PID %lu is no longer running, proceeding to launch Anki.\n", pid);

  // Check if we need to install a package or delete addon directory
  const char *package_file = NULL;
  if (ends_with(addon_dir_or_package, ".ankiaddon")) {
    printf("Installing addon from package %s...\n", addon_dir_or_package);
    package_file = addon_dir_or_package;
  } else {
    printf("Deleting addon directory %s...\n", addon_dir_or_package);
    if (!send_to_trash(addon_dir_or_package)) {
      printf("Warning: Failed to delete addon directory\n");
    }
  }

  // Launch Anki
  if (!launch_anki(anki_exe, anki_base, package_file)) {
    printf("Error: Failed to launch Anki\n");
    return 1;
  }

  printf("Anki launched successfully\n");
  return 0;
}
