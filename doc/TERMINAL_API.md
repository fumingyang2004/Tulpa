# Linux terminal management API

Implementation contract for the Linux CLI and the Claude Code terminal frontend.
The existing Tulpa MCP tools, persona snapshots, group learning, permissions and
OneBot event loop remain the application core. SnowLuma runs independently.

Managed SnowLuma also supports an owned Docker instance on Linux. Its desktop
management routes are authenticated by the terminal service like the existing
local-folder routes. See [DOCKER_MANAGED_PLAN.md](DOCKER_MANAGED_PLAN.md) for the
Docker request/response contract. The `docker-login` POST is the sole management
response containing the noVNC password; never log or cache its response. Neither
Docker management nor credential retrieval is exposed as an Agent/MCP tool.

## Client interface

Backend-owned `chatlocal.terminal_client.TerminalClient(base_url=None,
token_file=None, timeout=12)` exposes synchronous `get(path, **params)`,
`post(path, body=None)`, `put(path, body)` and `close()` methods. It loads the
local management credential itself. `TerminalError` is a safe user-facing
exception; `status_code` is an HTTP status when the service answered, otherwise
None (e.g. connection failure). Paths below are relative to `/api/terminal`. Run network calls in
Textual workers, not in render or the UI event loop. The client uses loopback
only, no proxy, with bounded timeouts. Default address: http://127.0.0.1:7861.

Management uses a different Bearer credential from MCP client grants; stored
with owner-only access in `data/terminal-admin.token`. It is never returned by
an API. Default state stays in the checkout's data directory for compatibility.

## Endpoints

- `GET /instances`: hub-only `{items:[descriptor],current_id:"default"}`. A descriptor
  contains `id,name,root,management_port,mcp_port,url,token_file,default,running,state,error,mcp`.
  `state` is `running`, `stopped` or `error`; paths identify local files, never contain tokens.
  Directory ownership errors may omit connection paths. This read never starts an instance.
- `POST /instances {id,name,management_port?,mcp_port?}`: creates a stopped clean instance
  and returns its descriptor. IDs match `[a-z][a-z0-9-]{0,31}`, excluding `default`.
  Unique free management/MCP ports auto-allocate from 7862/18778. Only public distribution
  source and the shared `.venv` are reused. Per-instance settings, credentials and stores
  are new. MCP starts disabled at the reserved port; SL requires its own explicit setup.
- `PUT /instances/{id} {name}`: rename without moving data or changing ports; includes default.
- `POST /instances/{id}/start {}` and `/stop {}`: manage that child's backend and return
  a fresh descriptor. Start reuses a running backend; stopped children receive current hub
  public source first. Independent user systemd units are used when available, without
  enabling autostart. Stop also stops the child's managed SL. The default hub cannot stop
  itself via these endpoints; use `tulpa stop` in its own directory. Child APIs reject hub
  operations. Instance creation/start/stop can take up to 60 seconds in the management client.
- `TerminalClient.for_instance(descriptor)` creates a client bound to that instance's
  local URL/token file. Keep a separate hub client for instance management. UI callbacks
  must capture the selected client before dispatch, and ignore results from an earlier
  selection. Do not carry credentials, rows or polling jobs across an instance switch.
- `GET /overview`: `{version, platform, mcp:{running,enabled,port,url,error},
  sl:{http_url,ws_url,has_http_token,has_ws_token,configured},
  counts:{messages,connections,active_sessions}, capabilities:{native_import,
  managed_snowluma,multi_instance}, receiver:{state,note}, storage, instance:{id,name}}`.
- `POST /shutdown {}`: `{stopping:true}`. Authenticated local management only;
  gracefully stops a backend launched by `tulpa serve`. An externally hosted
  application without a shutdown callback returns 409. Not exposed through MCP.
- `GET /sl`: same SL fields as above.
- `PUT /sl`: `{http_url,ws_url,http_token?,ws_token?}`. Omitted/empty tokens
  preserve existing values. Tokens are password inputs, never placed in logs.
  Saving does not start SnowLuma. Returns the masked SL configuration.
  Changing the connection settings stops existing chat sessions and their
  receiver so the operator can verify the account before starting again.
- `POST /sl/test {}`: `{ok,account?,message,code?,http_ok?,receiver?}`. Read-only identity and
  event connectivity check; errors are displayed without credential contents.
- `GET /groups`: `{items:[{id,name,account}]}` from the current SL account.
  `id` is already the full conversation id, e.g. `111:group:222`; `account`
  is `111`. Preserve `id` verbatim in grant scopes and session requests.
- `PUT /mcp {enabled:bool,port:int}`: returns MCP status; no grants are created.
- `GET /grants`: `{items:[{id,name,scope,created_at,revoked,valid,calls,revision}]}`.
- `POST /grants`: native grant body `{name,platforms:["qq"],
  conversations:[["qq","ACCOUNT:group:GROUP"]],start:"",end:"",onebot:bool,
  send:bool,manage:bool,chat:bool,chat_images:bool,chat_sticker_send:bool,
  chat_sticker_collect:bool,chat_reactions:bool,media:bool,prepare:bool,voice:bool}`.
  Every optional permission starts false. Continuous chat requires send=true;
  sticker send/collect requires chat_images=true. No groups means no grant.
  Returns `{id,token,mcp_url,client_config}`. Show this one-time credential in an
  explicit modal, never in status/event logs. Existing tokens cannot be recovered.
  `client_config` is a Claude Code JSON example with `mcpServers.tulpa.type="http"`,
  `url`, `headers.Authorization`, and `timeout=240000`; other clients use their
  own configuration schema with the same MCP URL and Bearer credential.
- `POST /grants/{id}/revoke {}`: `{ok:true}`; confirmation names the connection.
- `PUT /grants/{id}`: partial permission update, for example
  `{chat_voice:true,expected_revision:0}`. Only the existing permission booleans
  and the optional integer `expected_revision` are accepted; omitted flags remain
  unchanged. Returns `{ok:true,id,scope,revision,changed,stopped_sessions,note}`
  without a token. The token, id, source/account binding, name, conversation range
  and dates are preserved. Dependencies are validated after merging. Enabling live
  QQ permissions verifies the same SL account; reductions work offline.
  Revoked connections cannot be edited. A stale revision returns 409 without saving.
  An actual change increments revision, stops this grant's active sessions and
  invalidates pending calls/plans; a no-op leaves sessions running. External
  operations already dispatched retain their receipts. The TUI submits the revision
  from its displayed row; the CLI fetches one before sending unless explicitly given.
  Native local management also exposes `PUT /api/mcp/connections/{id}`; neither
  endpoint is an MCP model tool. Refresh/reconnect clients that cache tool lists.
- `GET /personas`: `{items:[{id,name,description,content}]}`.
- `GET /sessions`: `{items:[{id,name,conversation_id,grant_id,active,persona_name,
  persona_preset,participation,state,note,stop_reason,created,updated}]}`;
  additional native Tulpa fields may be present.
- `POST /sessions/start`: `{connection_id,conversation_id,persona_preset,
  idempotency_key}` -> `{session_id,note}`. This prepares a Tulpa session; the
  external MCP Agent still has to run. UI says “等待 Agent”, never “模型已启动”.
- `POST /sessions/{id}/stop {}`: native stop result. Stops this continuous-chat
  session, including its pending replies/reactions; it does not revoke the grant's
  separate permissions. Revoke the connection to remove its other access.
- `GET /sessions/{id}/learning`: native learning status JSON (display summary
  plus readable JSON details if the shape is not known).
- `PUT /sessions/{id}/learning {enabled:bool}`: native learning status.
- `GET /search?query=...&limit=20`: `{messages:[{id,platform,conversation,
  conversation_id,sender,timestamp,content}]}`. Searches the operator's imported
  local index; not all historical QQ messages are automatically imported.
- `GET /events`: `{items:[{id,name,tool,at,status,seconds}]}` recent tool calls.
- `POST /import {path,platform?}`: imports an existing JSON/JSONL file under
  this checkout's `imports/` directory and returns native import counts.

Failures use HTTP error statuses with `{detail:"safe message"}`. Offline is a
first-class screen state with a `tulpa serve` hint and retry, not fake sample data.

## Frontend ownership

SL extension backend contract:

- `GET /friends`: same item shape as `/groups`, with `ACCOUNT:direct:FRIEND` IDs.
- `GET /grant-permissions`: `{items:[{id,label,scope:"account"|"conversation",default:false}]}`.
  The extension flags are `chat_voice`, `chat_video`, `chat_files`, `qq_messages`,
  `qq_interactions`, `qq_friends`, `qq_profile`, `qq_qzone`, `qq_albums`.
- `POST /grants` accepts those boolean flags in addition to existing permissions;
  new flags default false. `qq_friends`, `qq_profile`, `qq_qzone` authorize
  account-wide actions, including contacts outside selected groups. Explain this
  scope in the form. Attachment flags require `chat:true,send:true`.
- The existing `conversations` selection and session `conversation_id` also
  accept verified friend IDs. Temporary private sessions are excluded.
- Existing grants remain unchanged. Both the CLI and TUI expose the new options.
  The TUI reads `/grant-permissions` before offering extension flags; failures
  of this endpoint or `/friends` show a notice and preserve the original group flow.

See [SL_TOOLS.md](SL_TOOLS.md) for MCP capabilities, examples, and limitations.

Claude Code owns `chatlocal/tui/` and the TUI checks, including `scripts/check_tui_instances.py`.
Codex owns the backend, client, launcher, requirements, shared docs and Linux
adaptations. UI entry: `chatlocal.tui.run(base_url=None, token_file=None, initial_instance=None)`;
`initial_instance` is an optional descriptor selected on mount while retaining the hub client.
Allow dependency injection of a client for headless UI tests.

Default `./tulpa` prepares missing dependencies, starts or reuses the backend,
waits for authenticated readiness, then opens the TUI. Concurrent launches share
an instance lock. Owned systemd units are reused without installing or replacing
units; otherwise the backend detaches with private logs in `.tmp/terminal-service.log`.
`./tulpa start` starts only the backend; `./tulpa stop` verifies the returned storage
directory before stopping this installation. `./tulpa serve` remains the foreground
entry for a supervisor. Closing the UI never stops the backend.

## Full Linux port (implementation contract)

Existing `/api/terminal` paths remain compatible. `TerminalClient` will also
expose `api_get(path, **params)`, `api_post(path, body=None)`,
`api_put(path, body)`, `api_patch(path, body)`, `api_delete(path, body=None)`
for explicit paths starting `/api/` (not URLs). These use the same local admin
credential and add the original UI headers. They return the native JSON shape
(dict or list); HTTP errors remain `TerminalError`. `download(path, output)`
saves a server response to a new, owner-only local file without overwriting.

The terminal backend mounts the original routes from `chat_view.py`,
`reply_routes.py`, `group_admin_routes.py`, `tulpa_routes.py`, `watch_routes.py`
(including data and live routes), `artifact_routes.py`, `voice_routes.py`,
`investigation_routes.py`, `workspace_routes.py`, `desktop_routes.py`, and
`optional_components.py`. Read those modules for exact native request/response
schemas, revisions, confirmations and async job polling. Do not invent fields
or bypass proposal/preview/confirm workflows. Raw HTML fields may be displayed
as plain text or replaced by the accompanying structured message fields.

New `/api/terminal` endpoints:

- `GET /model`: `{api_base, model, has_api_key, reasoning_effort}`.
- `PUT /model`: `{api_base, model, api_key?, reasoning_effort?}`; blank key
  preserves it, `clear_api_key:true` explicitly removes it. Never return the key.
- `GET /agent/sessions`: `{items:[{id,title,updated}]}`.
- `POST /agent/sessions {}`: `{id}`.
- `GET /agent/sessions/{id}`: `{id,turns:[{id,question,status,record,options,...}]}`.
- `DELETE /agent/sessions/{id} {}`: `{ok:true}` (refuses while a turn is running).
- `POST /agent/sessions/{id}/ask`:
  `{question,platforms?:["qq","wechat"],conversations?:[],start?:"",end?:"",
  profile?:"standard",vision?:"ocr",include_reasoning?:false}` -> `{turn_id}`.
  Explicit submission invokes the configured model. Poll session history;
  running records contain `events` and completed records include `result`.
- `POST /agent/turns/{id}/stop {}`: `{ok:true}`.
- `GET /components`: actual status of readers, ASR, OCR, Office and Linux service.
- `GET /native`: `{platforms:{qq:{source,data_root,keys_file,configured,source_type,source_label,requires_key},wechat:{...}}}`.
  QQ Docker sources also report `source_ready`, `source_account` and `detail`.
- `PUT /native/{platform}`: `{source?:"auto"|"database"|"snowluma",data_root?,keys_file?}`;
  `snowluma` is QQ-only and reads the owned Docker instance through read APIs.
  Omitted fields retain their values; an empty path clears it. Database mode accepts
  a local client root and an optional key JSON path. Keys are read locally and never returned.
- `POST /native/install {}`: start pinned reader installation; returns `{job_id}`.
- `POST /native/{platform}/extract {account?,method:"passive"|"active",confirm_start_client?:false}`:
  returns `{job_id}`. Active requires explicit `confirm_start_client:true`, a
  supported x86_64 client and normal exit of the existing client. Never auto
  select Active after a passive failure. No keys are returned. ARM64 supports
  native reads with verified supplied/cached keys; active capture is unavailable.
- `GET /jobs/{id}`: `{id,status,detail,error?}` for component install jobs.
- `POST /components/{name}/install {}` (`voice`, `readers`, `ocr`, `office`):
  install missing runtime/dependencies only following explicit user selection.
- `GET /service`: Linux service state; UI displays it, CLI owns installation.

All mutating operations remain explicit buttons/forms. Sending a reply requires
the native preview token and a separate confirmation of the exact text. Creating
MCP grants retains separate permissions. Workspace application retains proposal
IDs, revision checks and confirmation. Deletion asks for the specific item.

Frontend expansion: model chat/settings, native sources/import/live controls,
chat browsing and reply review, memories, watch cards, artifacts/collections,
workspaces/proposals, SL managed setup and optional components. Reuse the current
sidebar with scrolling and visible labels; preserve the existing 1–7 shortcuts.
Provide proper forms and list/detail flows, not a generic raw-JSON API console.

`GET /components` returns a mapping: `agent`, `readers`, `ocr`, `office` and
`voice` each have `installed:bool`. Readers also report `architecture`,
`active_key_supported`, `detail`. Voice reports `executable:bool`,
`model:{status,downloaded,total,message}`, `build_tools:bool`. `service` contains
`available`, `installed`, `active`, optionally `enabled/state/substate/detail`.
