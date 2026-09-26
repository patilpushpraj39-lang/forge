alter table runs
    add column source_snapshot_sha256 text,
    add column source_snapshot_size_bytes bigint,
    add column source_snapshot_media_type text;

alter table runs
    add constraint runs_source_snapshot_check check (
        (
            source_snapshot_sha256 is null
            and source_snapshot_size_bytes is null
            and source_snapshot_media_type is null
        )
        or (
            source_snapshot_sha256 ~ '^[0-9a-f]{64}$'
            and source_snapshot_size_bytes > 0
            and source_snapshot_size_bytes <= 100000000
            and source_snapshot_media_type = 'application/vnd.forge.snapshot+tar'
        )
    );
