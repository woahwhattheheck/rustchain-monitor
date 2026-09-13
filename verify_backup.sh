#!/bin/bash
#
# RustChain Backup Verification Script
# Verifies SQLite backup integrity without modifying any data
#
# Usage: ./verify_backup.sh [--backup-dir /path/to/backups] [--live-db /path/to/live.db]
# 
# Exit codes: 0 = PASS, 1 = FAIL
#
# Payout: 10 RTC ( bounty #755 )
# Wallet: Include your wallet address when claiming

set -euo pipefail

# Configuration
BACKUP_DIR="${1:-${BACKUP_DIR:-/root/rustchain/backups}}"
LIVE_DB="${2:-${LIVE_DB:-/root/rustchain/rustchain_v2.db}}"
TEMP_DIR="/tmp/backup_verify_$$"

# Tables to verify
REQUIRED_TABLES=("balances" "miner_attest_recent" "headers" "ledger" "epoch_rewards")

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

cleanup() {
    rm -rf "$TEMP_DIR"
}
trap cleanup EXIT

# Find latest backup
find_latest_backup() {
    local latest
    
    # Keep the wildcard outside quotes so Bash expands the preferred
    # RustChain backup pattern before ls sees it. Storing the glob in a
    # quoted variable makes the '*' literal and silently skips this tier.
    latest=$(ls -1t "$BACKUP_DIR"/rustchain_v2*.db.bak 2>/dev/null | head -1) || true
    
    if [[ -z "$latest" ]]; then
        # Try alternative patterns
        latest=$(ls -1t "$BACKUP_DIR"/*.bak 2>/dev/null | head -1) || true
    fi
    
    if [[ -z "$latest" || ! -f "$latest" ]]; then
        echo "ERROR: No backup file found in $BACKUP_DIR"
        exit 1
    fi
    
    echo "$latest"
}

# Main verification
main() {
    local backup_file
    backup_file=$(find_latest_backup)
    
    log "Backup: $backup_file"
    
    # Create temp directory
    mkdir -p "$TEMP_DIR"
    
    # Copy backup to temp location (non-destructive)
    local test_db="$TEMP_DIR/backup_test.db"
    cp "$backup_file" "$test_db"
    
    # Run integrity check
    local integrity_result
    integrity_result=$(sqlite3 "$test_db" "PRAGMA integrity_check;" 2>&1)
    
    if [[ "$integrity_result" != "ok" ]]; then
        log "Integrity: FAIL"
        log "ERROR: $integrity_result"
        log "RESULT: FAIL"
        exit 1
    fi
    log "Integrity: PASS"
    
    # Check required tables exist and have data
    local all_passed=true
    
    for table in "${REQUIRED_TABLES[@]}"; do
        local backup_count live_count
        
        # Required backup tables must be queryable and non-empty. Do not turn
        # query failures into a zero row count: a missing/corrupt table is a
        # failed backup even when the live side is also unavailable or empty.
        if ! backup_count=$(sqlite3 "$test_db" "SELECT COUNT(*) FROM $table;" 2>/dev/null); then
            log "$table: ❌ (missing or unreadable in backup)"
            all_passed=false
            continue
        fi
        if [[ ! "$backup_count" =~ ^[0-9]+$ ]] || [[ "$backup_count" -le 0 ]]; then
            log "$table: ${backup_count:-invalid} rows ❌ (empty or invalid!)"
            all_passed=false
            continue
        fi
        
        # Get row count from live DB (if available). Live comparison is
        # advisory; failure to query the live DB must not disguise backup
        # validity, nor should it make an otherwise valid backup fail.
        if [[ -f "$LIVE_DB" ]]; then
            if ! live_count=$(sqlite3 "$LIVE_DB" "SELECT COUNT(*) FROM $table;" 2>/dev/null); then
                log "$table: $backup_count rows ✅ (live comparison unavailable)"
                continue
            fi
            if [[ ! "$live_count" =~ ^[0-9]+$ ]]; then
                log "$table: $backup_count rows ✅ (live count invalid; comparison unavailable)"
                continue
            fi
            
            # Allow up to 1 epoch behind (~600 seconds of data = ~10 blocks)
            local max_diff=20
            local diff=$((live_count - backup_count))
            
            if [[ $diff -lt 0 ]]; then
                diff=$((-diff))
            fi
            
            if [[ $diff -le $max_diff ]]; then
                log "$table: $backup_count rows (live: $live_count) ✅"
            else
                log "$table: $backup_count rows (live: $live_count) ⚠️ (>$max_diff behind)"
            fi
        else
            log "$table: $backup_count rows ✅"
        fi
    done
    
    # Final result
    if [[ "$all_passed" == true ]]; then
        log "RESULT: PASS"
        exit 0
    else
        log "RESULT: FAIL"
        exit 1
    fi
}

# Run with optional arguments
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
