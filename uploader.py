#!/usr/bin/env python3
"""reMarkable uploader using rmapi."""

import os
import subprocess
from pathlib import Path


class RemarkableUploader:
    """Handles uploading files to reMarkable using rmapi."""

    def __init__(self, rmapi_path: str, folder: str) -> None:
        self.rmapi_path = rmapi_path
        self.folder = folder
        self._ensure_rmapi_available()
        self._ensure_folder_exists()

    def _ensure_rmapi_available(self) -> None:
        """Check if rmapi is available."""
        try:
            subprocess.run(
                [self.rmapi_path, "version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            msg = (
                "rmapi not found or not working. Please install rmapi "
                "and ensure it's in PATH or set the correct path in config."
            )
            raise RuntimeError(msg)

    def _ensure_folder_exists(self) -> None:
        """Ensure the target folder exists on reMarkable."""
        try:
            # Check if folder exists
            result = subprocess.run(
                [self.rmapi_path, "find", self.folder],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0 or not result.stdout.strip():
                # Create folder
                print(f"Creating folder '{self.folder}' on reMarkable...")
                subprocess.run([self.rmapi_path, "mkdir", self.folder], check=True)
        except subprocess.CalledProcessError as e:
            print(f"Warning: Could not ensure folder exists: {e}")

    def upload_file(self, file_path: Path) -> bool:
        """Upload a file to reMarkable."""
        try:
            print(f"Uploading {file_path.name} to reMarkable...")

            # Change to temp directory to ensure relative path upload
            original_cwd = Path.cwd()
            try:
                os.chdir(file_path.parent)
                cmd = [self.rmapi_path, "put", file_path.name, self.folder]
                subprocess.run(cmd, capture_output=True, text=True, check=True)
            finally:
                os.chdir(original_cwd)

            print(f"Successfully uploaded {file_path.name}")
            return True

        except subprocess.CalledProcessError as e:
            print(f"Failed to upload {file_path.name}: {e}")
            if e.stderr:
                print(f"Error output: {e.stderr}")
            return False
