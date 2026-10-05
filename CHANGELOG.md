# Changelog

## [0.1.8](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.7...v0.1.8) (2026-10-05)


### Bug Fixes

* accept all STM32 families and harden ST-LINK target check ([#35](https://github.com/gabrielfrasantos/port-bridge/issues/35)) ([c45ca20](https://github.com/gabrielfrasantos/port-bridge/commit/c45ca20264308b586eb2f583250df7a7dcfbe1d5))

## [0.1.7](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.6...v0.1.7) (2026-10-05)


### Features

* STM32-only target device for ST-LINK probes ([#33](https://github.com/gabrielfrasantos/port-bridge/issues/33)) ([b78fdb7](https://github.com/gabrielfrasantos/port-bridge/commit/b78fdb77b8c20d53fe9a051f50273c5c9b1db5ca))

## [0.1.6](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.5...v0.1.6) (2026-10-04)


### Features

* multiple serial/CAN/probe channels and ST-LINK support ([#31](https://github.com/gabrielfrasantos/port-bridge/issues/31)) ([e2b717e](https://github.com/gabrielfrasantos/port-bridge/commit/e2b717ec67391bb3f1011f5c18f73e824305edc4))

## [0.1.5](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.4...v0.1.5) (2026-09-30)


### Bug Fixes

* report the real app version so the update check stops flagging 0.1.4 as outdated ([#29](https://github.com/gabrielfrasantos/port-bridge/issues/29)) ([96eaacd](https://github.com/gabrielfrasantos/port-bridge/commit/96eaacdabc6d5470fb05b5135babf9e86d91cfb6))

## [0.1.4](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.3...v0.1.4) (2026-09-29)


### Features

* add J-Link and OpenOCD debug probe bridges ([#28](https://github.com/gabrielfrasantos/port-bridge/issues/28)) ([32a4518](https://github.com/gabrielfrasantos/port-bridge/commit/32a4518b21b349f3c611faf5876e3a80129cbf06))


### Build System

* **deps:** Bump softprops/action-gh-release from 3.0.2 to 3.0.3 ([#14](https://github.com/gabrielfrasantos/port-bridge/issues/14)) ([4724cf7](https://github.com/gabrielfrasantos/port-bridge/commit/4724cf7961bc7cf7e8161a1765978c3e60cc77e8))

## [0.1.3](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.2...v0.1.3) (2026-09-08)


### Bug Fixes

* missing python-can hiddenimports and int(channel) on non-numeric… ([#13](https://github.com/gabrielfrasantos/port-bridge/issues/13)) ([410390e](https://github.com/gabrielfrasantos/port-bridge/commit/410390e729552592f458af2b75b9605aeb5aed98))
* wire build-installers into release-please via workflow_call ([#11](https://github.com/gabrielfrasantos/port-bridge/issues/11)) ([7c8fe63](https://github.com/gabrielfrasantos/port-bridge/commit/7c8fe636e2c9ebed75ca729907bc07f3208409e7))

## [0.1.2](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.1...v0.1.2) (2026-09-08)


### Features

* add build-installers workflow for Windows and Linux ([#9](https://github.com/gabrielfrasantos/port-bridge/issues/9)) ([1efc2be](https://github.com/gabrielfrasantos/port-bridge/commit/1efc2bea24553fd6ec7c7fbeb611d487f70fc178))

## [0.1.1](https://github.com/gabrielfrasantos/port-bridge/compare/v0.1.0...v0.1.1) (2026-09-08)


### Features

* add GUI, agent instructions, CI workflows, docs, and release config ([09c6855](https://github.com/gabrielfrasantos/port-bridge/commit/09c685587ca845ed5ec86f1344d67346d0403cae))
* initial project structure — portbridge package with full test suite ([e233fc5](https://github.com/gabrielfrasantos/port-bridge/commit/e233fc54cde91e7b049f360fb21d4f1402ad7426))
* system tray icon, auto-update checker, and PyInstaller packaging ([#8](https://github.com/gabrielfrasantos/port-bridge/issues/8)) ([6a43ed8](https://github.com/gabrielfrasantos/port-bridge/commit/6a43ed814446f20a6943648fec4d01ba43955c97))


### Bug Fixes

* resolve all mypy strict errors ([fa4f858](https://github.com/gabrielfrasantos/port-bridge/commit/fa4f85884303af6fb6063855d89f7780e48e91ea))
* resolve all remaining ruff lint errors ([c7518b2](https://github.com/gabrielfrasantos/port-bridge/commit/c7518b203710f2e1290affe5ffd910cfc66b4220))
* resolve all ruff lint errors (import order, line length, E402) ([c7277be](https://github.com/gabrielfrasantos/port-bridge/commit/c7277be892b1bb6fdce658ca8c3812513a4c9b02))
* resolve mypy errors and IDE diagnostics in can_server.py ([313ad14](https://github.com/gabrielfrasantos/port-bridge/commit/313ad1434fc730325c9033df860932abae7bcbcb))
* resolve remaining ruff errors in gui and test files ([7443383](https://github.com/gabrielfrasantos/port-bridge/commit/74433836f644503ae38cf5d48404ceacaac0212c))


### Continuous Integration

* pin all GitHub Actions to commit hashes ([5256fc5](https://github.com/gabrielfrasantos/port-bridge/commit/5256fc579e0b4a37156b1e846c6bc4317f4c3614))


### Build System

* **deps:** Bump softprops/action-gh-release from 3.0.2 to 3.0.3 ([#4](https://github.com/gabrielfrasantos/port-bridge/issues/4)) ([b45883f](https://github.com/gabrielfrasantos/port-bridge/commit/b45883f3f77c3fce94ea1f7868a073dbe64f3260))
