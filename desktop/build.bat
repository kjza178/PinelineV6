@echo off
rem Dong goi installer NSIS -> desktop\src-tauri\target\release\bundle\nsis\
cd /d %~dp0src-tauri
cargo tauri build
