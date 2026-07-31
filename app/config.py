"""应用配置：全部环境变量在 pydantic-settings 中声明，字段名大写即环境变量名。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 核心 ----
    vault_path: Path = Path("/vault")
    port: int = 8080
    data_dir: Path = Path("/data")
    index_backend: str = "fts5"  # fts5 | meili
    meili_url: str = "http://meilisearch:7700"
    meili_master_key: str = ""

    # ---- LLM ----
    llm_provider: str = "deepseek"
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    llm_max_tokens: int = 4096
    llm_temperature: float = 0.2
    ollama_host: str = "http://host.docker.internal:11434"

    # ---- Vault 行为 ----
    ignore_dirs: str = ".obsidian,.trash,.git,node_modules,.stfolder"
    ignore_files: str = ""
    max_file_bytes: int = 5 * 1024 * 1024
    watch_enabled: bool = True
    watch_debounce_seconds: int = 2

    # ---- 安全 ----
    auth_token: str = ""
    disallowed_write_dirs: str = ".obsidian,.trash,.git"

    # ---- 备份 ----
    backup_enabled: bool = True
    backup_dir: Path | None = None  # 默认 {data_dir}/backups
    backup_schedule: str = "0 2 * * *"
    backup_retention: str = "7d,4w,3m"
    backup_max_bytes: int = 10 * 1024 * 1024
    backup_verify: bool = True

    # ---- 索引性能 ----
    index_batch_size: int = 200
    index_workers: int = 2
    jieba_userdict: str = ""  # jieba 自定义词典路径（如 data/userdict.txt，空 = 不加载）

    @property
    def ignore_dirs_list(self) -> list[str]:
        return [d.strip() for d in self.ignore_dirs.split(",") if d.strip()]

    @property
    def ignore_files_list(self) -> list[str]:
        return [f.strip() for f in self.ignore_files.split(",") if f.strip()]

    @property
    def disallowed_write_dirs_list(self) -> list[str]:
        return [d.strip() for d in self.disallowed_write_dirs.split(",") if d.strip()]

    @property
    def resolved_backup_dir(self) -> Path:
        return self.backup_dir or (self.data_dir / "backups")

    @property
    def llm_resolved_model(self) -> str:
        """按 provider 回退默认模型，避免未配置时启动即崩。"""
        if self.llm_model:
            return self.llm_model
        defaults = {
            "deepseek": "deepseek-chat",
            "openai": "gpt-4o-mini",
            "kimiapi": "moonshot-v1-8k",
            "ollama": "qwen2.5:7b",
        }
        return defaults.get(self.llm_provider, "deepseek-chat")

    def validate_paths(self) -> None:
        """启动前路径校验。

        坑 #11：备份目录若位于 vault 内部，会被索引、被递归备份、被恢复覆盖
        → 直接报错拒绝启动（防备份链断裂）。
        """
        vault = self.vault_path.expanduser().resolve()
        backup = self.resolved_backup_dir.expanduser().resolve()
        try:
            backup.relative_to(vault)
        except ValueError:
            pass
        else:
            raise ValueError(
                f"配置错误：BACKUP_DIR({backup}) 不能位于 vault({vault}) 内部。"
                f"请将备份目录放到 vault 之外的路径（如 {self.data_dir / 'backups'}）。"
            )

    def redacted(self) -> dict:
        """脱敏后的配置视图（日志/`--check` 用），key 打码。"""
        data = self.model_dump()
        if data.get("llm_api_key"):
            data["llm_api_key"] = data["llm_api_key"][:4] + "****"
        if data.get("auth_token"):
            data["auth_token"] = "****"
        if data.get("meili_master_key"):
            data["meili_master_key"] = "****"
        return data


@lru_cache
def get_settings() -> Settings:
    return Settings()
