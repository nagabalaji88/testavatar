@echo off
REM ===================================================================
REM  Master Prompt Generator - single-file launcher for Windows
REM
REM  Usage:  run-mpg.bat [command]
REM
REM  With no command it starts automatically: the free open-source stack
REM  when no API keys are configured, your API-key stack when they are.
REM
REM    (none)    Auto-detect and start          (default)
REM    up        Start using API-key providers  (OpenAI / Anthropic / Gemini)
REM    local     Start using free open-source models (Ollama, no API keys)
REM    down      Stop the stack, keep the data
REM    restart   Recreate the application containers
REM    rebuild   Force a no-cache rebuild, then start
REM    logs      Tail logs from every service
REM    status    Show container and health state
REM    test      Run the backend test suite in a container
REM    clean     Stop and DELETE all volumes (destroys the database)
REM    help      Show this message
REM ===================================================================

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_NAME=Master Prompt Generator"
set "FRONTEND_PORT=8080"
set "BACKEND_PORT=8000"
set "EXIT_CODE=0"

REM Capture a literal carriage return. Values read out of a CRLF .env keep
REM their trailing CR, which would corrupt the URLs we build from them.
for /f %%R in ('copy /Z "%~dpf0" nul') do set "CR=%%R"

REM Pause before closing when the script was double-clicked rather than run
REM from an existing console, so the output stays readable.
set "LAUNCHED_BY_EXPLORER="
REM Delayed expansion keeps any ^& or ^| in the command line from being parsed.
echo !CMDCMDLINE! | findstr /i /c:"/c" >nul 2>&1 && set "LAUNCHED_BY_EXPLORER=1"

set "CMD=%~1"
if "%CMD%"=="" set "CMD=auto"

echo.
echo  ==========================================================
echo   %APP_NAME%
echo  ==========================================================
echo.

if /i "%CMD%"=="help" goto :show_help
if /i "%CMD%"=="-h"   goto :show_help
if /i "%CMD%"=="/?"   goto :show_help

call :check_docker    || goto :end
call :resolve_compose || goto :end
call :load_env        || goto :end

if /i "%CMD%"=="auto"    goto :cmd_auto
if /i "%CMD%"=="up"      goto :cmd_up
if /i "%CMD%"=="local"   goto :cmd_local
if /i "%CMD%"=="down"    goto :cmd_down
if /i "%CMD%"=="restart" goto :cmd_restart
if /i "%CMD%"=="rebuild" goto :cmd_rebuild
if /i "%CMD%"=="logs"    goto :cmd_logs
if /i "%CMD%"=="status"  goto :cmd_status
if /i "%CMD%"=="test"    goto :cmd_test
if /i "%CMD%"=="clean"   goto :cmd_clean

echo  [ERROR] Unknown command: %CMD%
echo.
goto :show_help


REM ------------------------------------------------------------------
REM  Preflight
REM ------------------------------------------------------------------
:check_docker
where docker >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Docker was not found on your PATH.
    echo.
    echo          Install Docker Desktop from:
    echo          https://www.docker.com/products/docker-desktop/
    echo.
    set "EXIT_CODE=1"
    exit /b 1
)

docker info >nul 2>&1
if errorlevel 1 (
    echo  [ERROR] Docker is installed but the engine is not responding.
    echo          Start Docker Desktop, wait for it to finish starting,
    echo          then run this script again.
    echo.
    set "EXIT_CODE=1"
    exit /b 1
)

echo  [ok]    Docker engine is running.
exit /b 0


:resolve_compose
REM Prefer the v2 plugin; fall back to the standalone v1 binary.
set "COMPOSE="
docker compose version >nul 2>&1
if not errorlevel 1 set "COMPOSE=docker compose"

if not defined COMPOSE (
    where docker-compose >nul 2>&1
    if not errorlevel 1 set "COMPOSE=docker-compose"
)

if not defined COMPOSE (
    echo  [ERROR] Neither "docker compose" nor "docker-compose" is available.
    echo          Update Docker Desktop to a version that ships Compose v2.
    echo.
    set "EXIT_CODE=1"
    exit /b 1
)

echo  [ok]    Using: !COMPOSE!
exit /b 0


REM ------------------------------------------------------------------
REM  Environment file: create it on first run, then read the ports back
REM ------------------------------------------------------------------
:load_env
if exist ".env" goto :read_ports

if not exist ".env.example" (
    echo  [ERROR] Neither .env nor .env.example was found.
    echo          Run this script from the master-prompt-generator folder.
    echo.
    set "EXIT_CODE=1"
    exit /b 1
)

echo  [info]  No .env found - creating one from .env.example.
copy /y ".env.example" ".env" >nul
call :generate_secret

:read_ports
for /f "usebackq eol=# tokens=1,* delims==" %%A in (".env") do (
    if /i "%%A"=="FRONTEND_PORT" call :set_port FRONTEND_PORT "%%B"
    if /i "%%A"=="BACKEND_PORT"  call :set_port BACKEND_PORT  "%%B"
)
exit /b 0


:generate_secret
REM Replace the placeholder secret so a fresh install never runs on the
REM value published in .env.example. Kept on one line: a caret continuation
REM inside a parenthesised block is not reliably parsed by cmd.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$b=[Security.Cryptography.RandomNumberGenerator]::GetBytes(36); $s=([Convert]::ToBase64String($b)) -replace '[+/=]','x'; (Get-Content '.env') -replace '^JWT_SECRET_KEY=.*', ('JWT_SECRET_KEY=' + $s) ^| Set-Content '.env'" >nul 2>&1

if errorlevel 1 (
    echo  [warn]  Could not auto-generate JWT_SECRET_KEY - edit .env by hand
    echo          before exposing this deployment to anyone else.
) else (
    echo  [ok]    Generated a random JWT_SECRET_KEY.
)
exit /b 0


:set_port
REM %1 = variable name, %2 = raw value (quoted, possibly with a trailing CR)
set "RAW=%~2"
if not defined RAW exit /b 0
if defined CR set "RAW=!RAW:%CR%=!"
set "RAW=!RAW: =!"
echo !RAW!| findstr /r /c:"^[0-9][0-9]*$" >nul 2>&1
if errorlevel 1 exit /b 0
set "%~1=!RAW!"
exit /b 0


:check_api_keys
set "HAS_KEY="
findstr /r /c:"^OPENAI_API_KEY=..*"    ".env" >nul 2>&1 && set "HAS_KEY=1"
findstr /r /c:"^ANTHROPIC_API_KEY=..*" ".env" >nul 2>&1 && set "HAS_KEY=1"
findstr /r /c:"^GEMINI_API_KEY=..*"    ".env" >nul 2>&1 && set "HAS_KEY=1"

if defined HAS_KEY (
    echo  [ok]    At least one provider API key is configured.
    exit /b 0
)

echo.
echo  [warn]  No provider API keys are set in .env
echo.
echo          The stack will start and the dashboard will load, but every
echo          run will fail at the generation stage until you add at least
echo          one of these to .env:
echo.
echo            OPENAI_API_KEY=sk-...
echo            ANTHROPIC_API_KEY=sk-ant-...
echo            GEMINI_API_KEY=...
echo.
choice /c YN /n /m "         Start anyway? [Y/N] "
if errorlevel 2 (
    echo.
    echo  Cancelled. Add a key to .env and run this script again.
    set "EXIT_CODE=1"
    exit /b 1
)
echo.
exit /b 0


REM ------------------------------------------------------------------
REM  Commands
REM ------------------------------------------------------------------
:cmd_auto
REM Choose the stack from what is actually configured, so a double-click just
REM works. Explicit `up` / `local` always override this.
call :detect_mode

if /i "!MODE!"=="local" (
    echo  [ok]    .env is configured for the open-source stack.
    goto :cmd_local
)
if /i "!MODE!"=="cloud" (
    echo  [ok]    API provider keys found in .env.
    goto :cmd_up
)

echo  No provider is configured yet. Choose how to run:
echo.
echo    [1] Free and open source  - runs Qwen2.5, Llama 3.1 and Mistral on
echo        this machine via Ollama. No API keys, no cost. Downloads about
echo        12 GB on first start and is slow without a GPU.
echo.
echo    [2] API providers         - OpenAI, Anthropic and Gemini. Fast and
echo        higher quality, but you must paste keys into .env and pay per run.
echo.
choice /c 12 /n /m "  Select [1/2]: "
echo.
if errorlevel 2 (
    echo  Add your keys to .env, then run: run-mpg.bat up
    echo.
    if not exist ".env" copy /y ".env.example" ".env" >nul
    start "" notepad ".env"
    goto :end
)
set "LOCAL_CONFIRMED=1"
goto :cmd_local


:detect_mode
REM local wins: an .env already pointed at the open-source registry is an
REM explicit choice, even if stale keys are still sitting in the file.
set "MODE="
if not exist ".env" exit /b 0
findstr /b /c:"MODEL_CONFIG_PATH=/srv/config/models.local.json" ".env" >nul 2>&1 && set "MODE=local"
if defined MODE exit /b 0
findstr /r /c:"^OPENAI_API_KEY=..*"    ".env" >nul 2>&1 && set "MODE=cloud"
findstr /r /c:"^ANTHROPIC_API_KEY=..*" ".env" >nul 2>&1 && set "MODE=cloud"
findstr /r /c:"^GEMINI_API_KEY=..*"    ".env" >nul 2>&1 && set "MODE=cloud"
exit /b 0


:cmd_up
call :check_api_keys || goto :end
echo.
echo  Building images and starting services. The first run also pulls
echo  PostgreSQL, Redis and Qdrant, so it can take several minutes.
echo.
%COMPOSE% up -d --build
if errorlevel 1 goto :compose_failed
goto :wait_and_open


:cmd_local
echo.
echo  Starting the open-source stack: open-weight models served locally by
echo  Ollama. No API keys and no per-token cost.
echo.
echo  [warn]  The first run downloads roughly 12 GB of model weights, and on a
echo          machine without a GPU a single consensus run can take tens of
echo          minutes. See .env.local.example for smaller model options.
echo.
if not exist ".env.local.example" (
    echo  [ERROR] .env.local.example is missing - run from the project folder.
    set "EXIT_CODE=1"
    goto :end
)

findstr /b /c:"MODEL_CONFIG_PATH=/srv/config/models.local.json" ".env" >nul 2>&1
if errorlevel 1 (
    echo  [info]  Configuring .env for the open-source stack.
    echo          The current .env is kept as .env.backup
    echo.
    if not defined LOCAL_CONFIRMED choice /c YN /n /m "         Continue? [Y/N] "
    if errorlevel 2 if not defined LOCAL_CONFIRMED (
        echo.
        echo  Cancelled. To do it by hand: copy .env.local.example to .env
        goto :end
    )
    if exist ".env" copy /y ".env" ".env.backup" >nul
    copy /y ".env.local.example" ".env" >nul
    call :generate_secret
    echo.
)

echo  Building and starting services with the 'local' profile...
echo.
%COMPOSE% --profile local up -d --build
if errorlevel 1 goto :compose_failed

echo.
echo  Pulling model weights - the slow part of a first run...
echo.
%COMPOSE% --profile local run --rm ollama-pull
if errorlevel 1 (
    echo.
    echo  [warn]  Model pull reported an error. Check connectivity and retry:
    echo            run-mpg.bat local
    echo.
)
goto :wait_and_open


:cmd_rebuild
call :check_api_keys || goto :end
echo.
echo  Rebuilding every image from scratch (no cache)...
echo.
%COMPOSE% build --no-cache
if errorlevel 1 goto :compose_failed
%COMPOSE% up -d
if errorlevel 1 goto :compose_failed
goto :wait_and_open


:cmd_restart
echo  Recreating the application containers...
echo.
%COMPOSE% up -d --force-recreate backend worker frontend
if errorlevel 1 goto :compose_failed
goto :wait_and_open


:cmd_down
echo  Stopping the stack (data volumes are preserved)...
echo.
%COMPOSE% --profile local down
if errorlevel 1 goto :compose_failed
echo.
echo  [ok]    Stopped. Your database and vector index are still on disk.
goto :end


:cmd_logs
echo  Tailing logs - press Ctrl+C to stop.
echo.
%COMPOSE% --profile local logs -f --tail=120
goto :end


:cmd_status
echo  Container state:
echo.
%COMPOSE% --profile local ps
echo.
echo  Backend health:
curl -fsS "http://localhost:%BACKEND_PORT%/api/v1/health"
if errorlevel 1 echo  (the backend is not answering on port %BACKEND_PORT%)
echo.
goto :end


:cmd_test
echo  Running the backend test suite in a container...
echo.
REM The image deliberately excludes tests, so mount them at run time.
%COMPOSE% run --rm --no-deps -v "%CD%\backend\tests:/srv/tests" backend python -m pytest tests -q
if errorlevel 1 (
    echo.
    echo  [warn]  The test run reported failures - see the output above.
    set "EXIT_CODE=1"
) else (
    echo.
    echo  [ok]    Backend tests passed.
)
goto :end


:cmd_clean
echo.
echo  [warn]  This DELETES the PostgreSQL database, the Redis queue and
echo          the Qdrant vector index. Every past run will be lost.
echo.
choice /c YN /n /m "         Are you sure? [Y/N] "
if errorlevel 2 (
    echo.
    echo  Cancelled - nothing was removed.
    goto :end
)
echo.
%COMPOSE% --profile local down -v --remove-orphans
if errorlevel 1 goto :compose_failed
echo.
echo  [ok]    Stack removed and all volumes deleted.
goto :end


REM ------------------------------------------------------------------
REM  Wait for the API to report healthy, then open the dashboard
REM ------------------------------------------------------------------
:wait_and_open
echo.
echo  Waiting for the API to become healthy (up to 3 minutes)...

set "READY="
for /l %%I in (1,1,60) do (
    if not defined READY (
        curl -fsS -o nul "http://localhost:%BACKEND_PORT%/health" >nul 2>&1
        if not errorlevel 1 (
            set "READY=1"
        ) else (
            timeout /t 3 /nobreak >nul
        )
    )
)

echo.
if not defined READY (
    echo  [warn]  The API did not report healthy in time. The images may
    echo          still be building. Check progress with:
    echo.
    echo            run-mpg.bat logs
    echo.
    set "EXIT_CODE=1"
    goto :end
)

echo  [ok]    API is healthy.
echo.
echo  ----------------------------------------------------------
echo    Dashboard    http://localhost:%FRONTEND_PORT%
echo    API docs     http://localhost:%BACKEND_PORT%/docs
echo    Metrics      http://localhost:%BACKEND_PORT%/metrics
echo  ----------------------------------------------------------
echo.
echo  First time here? Create an account on the sign-in screen,
echo  then launch a consensus run.
echo.
echo    run-mpg.bat logs    tail the pipeline logs
echo    run-mpg.bat down    stop the stack
echo.

start "" "http://localhost:%FRONTEND_PORT%"
goto :end


REM ------------------------------------------------------------------
REM  Errors and help
REM ------------------------------------------------------------------
:compose_failed
echo.
echo  [ERROR] Compose returned an error.
echo.
echo          Common causes:
echo            - a port is already in use; change FRONTEND_PORT,
echo              BACKEND_PORT, POSTGRES_PORT, REDIS_PORT or QDRANT_PORT
echo              in .env and try again
echo            - Docker Desktop ran out of disk or memory
echo.
echo          Read the output above, or run: run-mpg.bat logs
echo.
set "EXIT_CODE=1"
goto :end


:show_help
echo  Usage:  run-mpg.bat [command]
echo.
echo    (none)    Auto-detect and start          (default)
echo    up        Start using API-key providers  (OpenAI / Anthropic / Gemini)
echo    local     Start using free open-source models (Ollama, no API keys)
echo    down      Stop the stack, keep the data
echo    restart   Recreate the application containers
echo    rebuild   Force a no-cache rebuild, then start
echo    logs      Tail logs from every service
echo    status    Show container and health state
echo    test      Run the backend test suite in a container
echo    clean     Stop and DELETE all volumes (destroys the database)
echo    help      Show this message
echo.
echo  Services: frontend, backend, worker, postgres, redis, qdrant.
echo  Ports come from .env, created from .env.example on first run.
echo.
goto :end


:end
echo.
if defined LAUNCHED_BY_EXPLORER pause
endlocal & exit /b %EXIT_CODE%
