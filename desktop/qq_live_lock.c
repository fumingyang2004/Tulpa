/* Only loaded in the dedicated QQ page-reader process, before source opens.
 * Use the host's extension API, never DLL offsets or a second SQLite ABI.
 * This test control is deliberately pinned and checked; fail closed on drift.
 */
#include "sqlite3ext.h"
SQLITE_EXTENSION_INIT1

__declspec(dllexport) int sqlite3_qqlivelock_init(
    sqlite3 *db, char **error, const sqlite3_api_routines *api
) {
    unsigned int old;
    (void)db;
    SQLITE_EXTENSION_INIT2(api);
    if (sqlite3_libversion_number() != 3053004 || !api->test_control) {
        *error = sqlite3_mprintf("qq_live_runtime_mismatch");
        return SQLITE_ERROR;
    }
    old = (unsigned int)sqlite3_test_control(SQLITE_TESTCTRL_PENDING_BYTE, 0u);
    if (old != 0x40000000u && old != 0x3ffffc00u) {
        *error = sqlite3_mprintf("qq_live_lock_unavailable");
        return SQLITE_ERROR;
    }
    sqlite3_test_control(SQLITE_TESTCTRL_PENDING_BYTE, 0x3ffffc00u);
    if ((unsigned int)sqlite3_test_control(SQLITE_TESTCTRL_PENDING_BYTE, 0u) != 0x3ffffc00u) {
        *error = sqlite3_mprintf("qq_live_lock_unavailable");
        return SQLITE_ERROR;
    }
    return SQLITE_OK;
}
