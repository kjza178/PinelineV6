@echo off
rem Chay app desktop V6 o che do dev (can Rust + tauri-cli: cargo install tauri-cli --version "^2")
cd /d %~dp0src-tauri
cargo tauri dev
