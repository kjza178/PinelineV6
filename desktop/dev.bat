@echo off
rem Chạy app desktop V6 ở chế độ dev (cần Rust + tauri-cli: cargo install tauri-cli --version "^2")
cd /d %~dp0src-tauri
cargo tauri dev
