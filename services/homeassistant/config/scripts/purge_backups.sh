#!/bin/bash

# Folder to clean up
TARGET_FOLDER="/config/backups"

# Find and delete files older than the specified age
find "$TARGET_FOLDER" -type f -mtime +90 -exec rm -f {} \;