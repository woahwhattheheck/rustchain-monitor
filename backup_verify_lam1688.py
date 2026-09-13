#!/usr/bin/env python3
"""
RustChain Backup Verification Tool
Automated validation of database backups

Bounty #755 - Automated Backup Verification
"""

import os
import sys
import json
import hashlib
import stat
import subprocess
import argparse
import tempfile
from datetime import datetime
from pathlib import Path


def _is_single_link_regular_file(path):
    """Return True only for an ordinary, non-aliased regular file."""
    try:
        file_stat = Path(path).lstat()
    except FileNotFoundError:
        return False
    return stat.S_ISREG(file_stat.st_mode) and file_stat.st_nlink == 1


class BackupVerifier:
    """Verify RustChain database backups integrity"""
    
    def __init__(self, backup_path, node_url="https://50.28.86.131"):
        self.backup_path = Path(backup_path)
        self.node_url = node_url
        self.results = {
            "timestamp": datetime.now().isoformat(),
            "backup_path": str(backup_path),
            "checksums": {},
            "integrity": False,
            "restoration": False,
            "errors": []
        }

    def validate_backup_root(self):
        """Require the selected backup entry itself to be a real directory."""
        try:
            root_stat = self.backup_path.lstat()
        except FileNotFoundError:
            error = "Invalid backup root: path does not exist"
            self.results["errors"].append(error)
            print(f"✗ {error}")
            return False

        if not stat.S_ISDIR(root_stat.st_mode):
            error = "Unsafe backup root: expected a real directory"
            self.results["errors"].append(error)
            print(f"✗ {error}")
            return False

        return True
    
    def calculate_checksum(self, file_path):
        """Calculate SHA256 checksum of file"""
        sha256_hash = hashlib.sha256()
        try:
            with open(file_path, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            return sha256_hash.hexdigest()
        except Exception as e:
            self.results["errors"].append(f"Error calculating checksum: {e}")
            return None
    
    def verify_backup_files(self):
        """Verify all backup files exist and have valid checksums"""
        required_files = ["rustchain.db", "wallets.db", "miner_state.json"]
        
        print(f"Checking backup directory: {self.backup_path}")
        
        for filename in required_files:
            filepath = self.backup_path / filename
            if _is_single_link_regular_file(filepath):
                checksum = self.calculate_checksum(filepath)
                self.results["checksums"][filename] = checksum
                if checksum is None:
                    print(f"✗ {filename}: CHECKSUM FAILED")
                    continue
                print(f"✓ {filename}: {checksum[:16]}...")
            elif os.path.lexists(filepath):
                self.results["errors"].append(f"Unsafe file type or link: {filename}")
                print(f"✗ {filename}: UNSAFE FILE TYPE OR LINK")
            else:
                self.results["errors"].append(f"Missing file: {filename}")
                print(f"✗ {filename}: NOT FOUND")
        
        return len(self.results["errors"]) == 0
    
    def verify_database_integrity(self):
        """Verify SQLite database integrity"""
        db_file = self.backup_path / "rustchain.db"
        self.results["integrity"] = False
        if not os.path.lexists(db_file):
            return False
        if not _is_single_link_regular_file(db_file):
            error = "Unsafe database file type or link: rustchain.db"
            self.results["errors"].append(error)
            print(f"✗ {error}")
            return False
        
        try:
            # PRAGMA integrity_check succeeds only when sqlite3 exits cleanly
            # and emits exactly "ok". Treat every other result as a failed
            # verification rather than accepting a truthy-looking substring.
            result = subprocess.run(
                ["sqlite3", str(db_file), "PRAGMA integrity_check;"],
                capture_output=True,
                text=True,
                timeout=30
            )
            stdout = result.stdout.strip()
            if result.returncode == 0 and stdout.lower() == "ok":
                self.results["integrity"] = True
                print("✓ Database integrity check passed")
                return True

            details = (result.stderr or result.stdout).strip()
            if not details:
                details = f"sqlite3 exited with status {result.returncode}"
            self.results["errors"].append(f"Database integrity failed: {details}")
            print(f"✗ Database integrity check failed: {details}")
            return False
        except FileNotFoundError:
            error = "sqlite3 not available; cannot verify database integrity"
            self.results["errors"].append(error)
            print(f"✗ {error}")
            return False
        except Exception as e:
            self.results["errors"].append(f"Integrity check error: {e}")
            return False
    
    def test_restoration(self):
        """Test backup restoration procedure"""
        print("\nTesting restoration procedure...")
        self.results["restoration"] = False
        
        try:
            # Use a uniquely owned scratch directory so verification never
            # collides with another run or removes pre-existing sibling files.
            with tempfile.TemporaryDirectory(
                prefix="rustchain_restore_",
                dir=self.backup_path.parent,
            ) as temp_name:
                temp_dir = Path(temp_name)
                restored_pairs = []

                # Copy only ordinary single-link files to temp. Linked or
                # special entries must never redirect restoration outside the
                # selected backup directory.
                for file in self.backup_path.glob("*"):
                    file_stat = file.lstat()
                    if stat.S_ISDIR(file_stat.st_mode):
                        continue
                    if not _is_single_link_regular_file(file):
                        raise ValueError(f"Unsafe backup entry: {file.name}")
                    dest = temp_dir / file.name
                    dest.write_bytes(file.read_bytes())
                    restored_pairs.append((file, dest))
                    print(f"✓ Restored {file.name} to test location")
                
                # Verify every expected restored copy remains an ordinary file
                # and exactly matches the source. Missing, truncated, or
                # same-length corrupted copies must fail closed.
                for original, restored in restored_pairs:
                    if not _is_single_link_regular_file(restored):
                        raise ValueError(f"Unsafe or missing restored file: {restored.name}")
                    if not _is_single_link_regular_file(original):
                        raise ValueError(f"Unsafe or missing source during restoration verification: {original.name}")
                    if restored.stat().st_size != original.stat().st_size:
                        raise ValueError(f"Restoration size mismatch: {restored.name}")

                    restored_checksum = self.calculate_checksum(restored)
                    original_checksum = self.calculate_checksum(original)
                    if restored_checksum is None or original_checksum is None:
                        raise ValueError(f"Unable to checksum restored entry: {restored.name}")
                    if restored_checksum != original_checksum:
                        raise ValueError(f"Restored content mismatch: {restored.name}")
                    print(f"✓ {restored.name} restoration verified")
                
            self.results["restoration"] = True
            return True
        except Exception as e:
            self.results["errors"].append(f"Restoration test failed: {e}")
            return False
    
    def generate_report(self):
        """Generate verification report"""
        report = f"""
=== RustChain Backup Verification Report ===
Generated: {self.results['timestamp']}
Backup Path: {self.results['backup_path']}

## Checksums
"""
        for filename, checksum in self.results["checksums"].items():
            report += f"- {filename}: {checksum}\n"
        
        report += f"""
## Integrity Status
{'✓ PASSED' if self.results['integrity'] else '✗ FAILED'}

## Restoration Test
{'✓ PASSED' if self.results['restoration'] else '✗ FAILED'}

## Errors
"""
        if self.results["errors"]:
            for error in self.results["errors"]:
                report += f"- {error}\n"
        else:
            report += "None\n"
        
        report += """
---
Wallet: Eud4bLR7sjwUviGkSA9w8Wg4PYY1api3Ybrjtsat3J4G
Bounty #755 - Automated Backup Verification
"""
        return report

    def save_report(self, report):
        """Atomically publish the report without following an existing link."""
        report_file = self.backup_path / "verification_report.txt"
        temp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.backup_path,
                prefix=".verification_report.",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                handle.write(report)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, report_file)
            temp_path = None
            return report_file
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
    
    def run(self):
        """Run full verification"""
        print("=" * 50)
        print("RustChain Backup Verification Tool")
        print("=" * 50)

        # Refuse a linked/non-directory backup root before reading backup
        # entries or publishing the verifier report through that path.
        if not self.validate_backup_root():
            report = self.generate_report()
            print(report)
            return self.results
        
        # Verify backup files
        self.verify_backup_files()
        
        # Verify database integrity
        self.verify_database_integrity()
        
        # Test restoration
        self.test_restoration()
        
        # Generate report
        report = self.generate_report()
        print(report)
        
        # Save report without following a pre-existing report symlink/hardlink.
        report_file = self.save_report(report)
        print(f"\nReport saved to: {report_file}")
        
        return self.results


def main():
    parser = argparse.ArgumentParser(description="RustChain Backup Verification Tool")
    parser.add_argument("backup_path", help="Path to backup directory")
    parser.add_argument("--node-url", default="https://50.28.86.131", help="RustChain node URL")
    
    args = parser.parse_args()
    
    verifier = BackupVerifier(args.backup_path, args.node_url)
    results = verifier.run()
    return 1 if results["errors"] or not results["integrity"] else 0


if __name__ == "__main__":
    sys.exit(main())