# Changelog

## 3.0.0-alpha

Initial public alpha of WangChuan standalone package.

### Added

- Standalone `wangchuan-memory` package layout
- Python API and CLI entry points
- Local SQLite-backed memory engine
- First-run database initialization
- Release safety check script
- Minimal pytest smoke suite
- GitHub Actions CI workflow

### Security

- Removed local `.env` and runtime databases from package
- Removed hardcoded provider credentials
- Added release checks for known secret signatures and local paths
