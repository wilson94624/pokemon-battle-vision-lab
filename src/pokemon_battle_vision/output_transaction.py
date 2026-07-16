"""Generated output directory 的安全、可回復 transactional replacement。"""

import os
import shutil
import stat
from pathlib import Path
from typing import Optional, Sequence
from uuid import uuid4

from .errors import InputError


def validate_generated_output_path(project_root: Path, output_dir: Path) -> Path:
    root = project_root.resolve()
    outputs_root = (root / "outputs").resolve()
    target = output_dir.resolve()
    dangerous = {Path("/").resolve(), Path.home().resolve(), root, outputs_root}
    if target in dangerous:
        raise InputError("拒絕清理危險 output path：{}".format(target))
    try:
        target.relative_to(outputs_root)
    except ValueError as exc:
        raise InputError(
            "Generated output 必須位於 repository 的 outputs/ 內：{}".format(target)
        ) from exc
    return target


class OutputTransaction:
    """先在同層 staging 建立並驗證，成功後才以 rename 替換正式目錄。"""

    def __init__(self, project_root: Path, output_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.target = validate_generated_output_path(self.project_root, output_dir)
        token = uuid4().hex
        self.staging_dir = self.target.parent / "{}.tmp-{}".format(self.target.name, token)
        self.backup_dir = self.target.parent / "{}.backup-{}".format(
            self.target.name, token
        )
        self.conflict_dir = self.target.with_name(self.target.name + " 2")
        self._committed = False

    @staticmethod
    def _tree(path: Path):
        if not path.exists():
            return
        yield path
        if path.is_dir():
            yield from path.rglob("*")

    @classmethod
    def clear_hidden_flags(cls, path: Path) -> None:
        """macOS Finder 會讀 BSD UF_HIDDEN；正式輸出必須逐項清除。"""
        # Finder 可能在可見 staging／正式目錄被檔案樹讀取時寫入 metadata；
        # 它不屬於生成結果，留著也會使 hidden validation 失敗。
        for item in list(cls._tree(path)):
            if item.name == ".DS_Store" and item.is_file():
                item.unlink()
        hidden_flag = getattr(stat, "UF_HIDDEN", 0)
        if not hidden_flag or not hasattr(os, "chflags"):
            return
        for item in cls._tree(path):
            flags = int(item.lstat().st_flags)
            if flags & hidden_flag:
                os.chflags(str(item), flags & ~hidden_flag, follow_symlinks=False)

    @classmethod
    def hidden_items(cls, path: Path):
        hidden_flag = getattr(stat, "UF_HIDDEN", 0)
        if not hidden_flag:
            return []
        return [
            str(item)
            for item in cls._tree(path)
            if int(getattr(item.lstat(), "st_flags", 0)) & hidden_flag
        ]

    @classmethod
    def validate_no_hidden_flags(cls, path: Path) -> None:
        hidden = cls.hidden_items(path)
        if hidden:
            raise InputError("Generated output 仍含 BSD hidden flag：{}".format(hidden[:5]))

    def _remove_empty_conflict_dir(self) -> None:
        if not self.conflict_dir.exists():
            return
        if not self.conflict_dir.is_dir() or any(self.conflict_dir.iterdir()):
            raise InputError(
                "拒絕覆蓋非空白 output 衝突目錄：{}".format(self.conflict_dir)
            )
        self.conflict_dir.rmdir()

    def _cleanup_orphans(self) -> None:
        prefixes = (
            "{}.tmp-".format(self.target.name),
            ".{}.tmp-".format(self.target.name),
        )
        backup_prefixes = (
            "{}.backup-".format(self.target.name),
            ".{}.backup-".format(self.target.name),
        )
        self.target.parent.mkdir(parents=True, exist_ok=True)
        for path in self.target.parent.iterdir():
            if path.name.startswith(prefixes):
                shutil.rmtree(str(path))
        backups = [
            path
            for path in self.target.parent.iterdir()
            if path.name.startswith(backup_prefixes)
        ]
        if not self.target.exists() and len(backups) == 1:
            os.replace(str(backups[0]), str(self.target))
            backups = []
        for path in backups:
            shutil.rmtree(str(path))

    def __enter__(self) -> "OutputTransaction":
        self._cleanup_orphans()
        self.staging_dir.mkdir(parents=False, exist_ok=False)
        return self

    def commit(self) -> None:
        if self._committed:
            raise RuntimeError("Output transaction 已 commit")
        if not self.staging_dir.is_dir():
            raise InputError("Output staging directory 遺失：{}".format(self.staging_dir))
        self.clear_hidden_flags(self.staging_dir)
        self.validate_no_hidden_flags(self.staging_dir)
        self._remove_empty_conflict_dir()
        moved_old = False
        if self.target.exists():
            os.replace(str(self.target), str(self.backup_dir))
            moved_old = True
        try:
            os.replace(str(self.staging_dir), str(self.target))
            self.clear_hidden_flags(self.target)
            self.validate_no_hidden_flags(self.target)
        except (OSError, InputError):
            if self.target.exists() and not self.staging_dir.exists():
                os.replace(str(self.target), str(self.staging_dir))
            if moved_old and self.backup_dir.exists() and not self.target.exists():
                os.replace(str(self.backup_dir), str(self.target))
            raise
        if self.backup_dir.exists():
            shutil.rmtree(str(self.backup_dir))
        self._committed = True

    @classmethod
    def commit_group(cls, transactions: Sequence["OutputTransaction"]) -> None:
        """多個同層 output 一起替換；任一 swap 失敗就全部回復舊版。"""
        selected = list(transactions)
        if not selected:
            raise InputError("Output transaction group 不可為空")
        targets = [transaction.target for transaction in selected]
        if len(targets) != len(set(targets)):
            raise InputError("Output transaction group target 不可重複")
        for transaction in selected:
            if transaction._committed:
                raise RuntimeError("Output transaction 已 commit")
            if not transaction.staging_dir.is_dir():
                raise InputError(
                    "Output staging directory 遺失：{}".format(transaction.staging_dir)
                )
            transaction.clear_hidden_flags(transaction.staging_dir)
            transaction.validate_no_hidden_flags(transaction.staging_dir)
            transaction._remove_empty_conflict_dir()

        moved_old = {id(transaction): False for transaction in selected}
        try:
            for transaction in selected:
                if transaction.target.exists():
                    os.replace(str(transaction.target), str(transaction.backup_dir))
                    moved_old[id(transaction)] = True
            for transaction in selected:
                os.replace(str(transaction.staging_dir), str(transaction.target))
                transaction.clear_hidden_flags(transaction.target)
                transaction.validate_no_hidden_flags(transaction.target)
        except (OSError, InputError):
            # 新版先移回 staging，再把所有舊版 backup 放回原位。
            for transaction in reversed(selected):
                if transaction.target.exists() and not transaction.staging_dir.exists():
                    os.replace(str(transaction.target), str(transaction.staging_dir))
            for transaction in reversed(selected):
                if (
                    moved_old[id(transaction)]
                    and transaction.backup_dir.exists()
                    and not transaction.target.exists()
                ):
                    os.replace(str(transaction.backup_dir), str(transaction.target))
            raise

        for transaction in selected:
            if transaction.backup_dir.exists():
                shutil.rmtree(str(transaction.backup_dir))
            transaction._committed = True

    def __exit__(self, exc_type, exc, traceback) -> Optional[bool]:
        if self.staging_dir.exists():
            shutil.rmtree(str(self.staging_dir))
        if self.backup_dir.exists() and not self.target.exists():
            os.replace(str(self.backup_dir), str(self.target))
        elif self.backup_dir.exists():
            shutil.rmtree(str(self.backup_dir))
        return None


def finalize_generated_output(path: Path) -> None:
    """replace 後再清理一次 Finder metadata，並執行最終 hidden gate。"""
    OutputTransaction.clear_hidden_flags(path)
    OutputTransaction.validate_no_hidden_flags(path)
