@echo off
rem Đóng gói installer NSIS → desktop\src-tauri\target\release\bundle\nsis\
cd /d %~dp0src-tauri
cargo tauri build
