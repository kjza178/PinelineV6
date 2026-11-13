@echo off
rem Cai doi subagent + slash command cho Claude Code vao .claude\ cua PinelineV2.
rem Chay lai bao nhieu lan cung duoc (ghi de ban cu trong .claude\agents, .claude\commands).
setlocal
cd /d "%~dp0.."
if not exist ".claude\agents" mkdir ".claude\agents"
if not exist ".claude\commands" mkdir ".claude\commands"
xcopy /Y /Q "claude_setup\agents\*.md" ".claude\agents\" >nul
xcopy /Y /Q "claude_setup\commands\*.md" ".claude\commands\" >nul
echo Da cai:
dir /B ".claude\agents"
dir /B ".claude\commands"
echo.
echo Mo lai Claude Code trong Z:\PinelineV2 roi go /agents de kiem tra.
pause
