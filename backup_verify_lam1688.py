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
import subprocess
import argparse
import tempfile
from datetime import datetime
from pathlib import Path

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
            "errors": []
        }
    
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
            if filepath.exists():
                checksum = self.calculate_checksum(filepath)
                self.results["checksums"][filename] = checksum
                if checksum is None:
                    print(f"✗ {filename}: CHECKSUM FAILED")
                    continue
                print(f"✓ {filename}: {checksum[:16]}...")
            else:
                self.results["errors"].append(f"Missing file: {filename}")
                print(f"✗ {filename}: NOT FOUND")
        
        return len(self.results["errors"]) == 0
    
    def verify_database_integrity(self):
        """Verify SQLite database integrity"""
        db_file = self.backup_path / "rustchain.db"
        self.results["integrity"] = False
        if not db_file.exists():
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
        
        try:
            # Use a uniquely owned scratch directory so verification never
            # collides with another run or removes pre-existing sibling files.
            with tempfile.TemporaryDirectory(
                prefix="rustchain_restore_",
                dir=self.backup_path.parent,
            ) as temp_name:
                temp_dir = Path(temp_name)

                # Copy files to temp for restoration test
                for file in self.backup_path.glob("*"):
                    if file.is_file():
                        dest = temp_dir / file.name
                        dest.write_bytes(file.read_bytes())
                        print(f"✓ Restored {file.name} to test location")
                
                # Verify restored files
                for file in temp_dir.glob("*"):
                    original = self.backup_path / file.name
                    if file.exists() and original.exists():
                        if file.stat().st_size == original.stat().st_size:
                            print(f"✓ {file.name} restoration verified")
                
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
{'✓ PASSED' if not self.results['errors'] else '✗ FAILED'}

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
    
    def run(self):
        """Run full verification"""
        print("=" * 50)
        print("RustChain Backup Verification Tool")
        print("=" * 50)
        
        # Verify backup files
        self.verify_backup_files()
        
        # Verify database integrity
        self.verify_database_integrity()
        
        # Test restoration
        self.test_restoration()
        
        # Generate report
        report = self.generate_report()
        print(report)
        
        # Save report
        report_file = self.backup_path / "verification_report.txt"
        with open(report_file, "w") as f:
            f.write(report)
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