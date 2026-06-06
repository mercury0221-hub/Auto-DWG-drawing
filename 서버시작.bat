@echo off
chcp 65001 > nul
title DWG 자동 작도 서버
cd /d "%~dp0"

echo ================================================
echo   DWG 자동 작도 서버
echo ================================================

REM ── .env 파일 존재 확인 ───────────────────────────
if not exist ".env" (
    echo.
    echo [오류] .env 파일이 없습니다.
    echo.
    echo    이 폴더에 .env 파일을 만들고 아래 내용을 입력하세요:
    echo    OPENROUTER_API_KEY=sk-or-v1-여기에_API키_입력
    echo.
    pause
    exit /b 1
)

REM ── 포트 중복 확인 ────────────────────────────────
netstat -ano | findstr ":5000 " | findstr "LISTENING" > nul 2>&1
if %errorlevel% == 0 (
    echo.
    echo [정보] 포트 5000이 이미 사용 중입니다 — 기존 서버를 재활용합니다.
    start "" "http://localhost:5000"
    goto :end
)

echo.
echo [정보] .env 파일에서 API 키를 자동으로 읽습니다.
echo [정보] 서버 시작 중... 브라우저가 자동으로 열립니다.
echo [정보] 종료하려면 이 창을 닫거나 Ctrl+C 를 누르세요.
echo.

C:\Users\user\AppData\Local\Programs\Python\Python314\python.exe server.py

:end
pause
