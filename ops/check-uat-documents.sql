/*
  Read-only check for the UAT source used by the portal.
  Run the whole script in SSMS against msk-db under the portal Windows account.
  It does not create or change any database objects or rows.
*/

USE [uat01_ivanov];
SET NOCOUNT ON;

DECLARE @FromDate date = '2026-10-01';
DECLARE @UntilDate date = DATEADD(day, 1, CONVERT(date, GETDATE()));
DECLARE @DateOffset int = (SELECT MIN(CONVERT(int, [Offset])) FROM [dbo].[_YearOffset]);
DECLARE @StoredFrom datetime2 = DATEADD(year, @DateOffset, CONVERT(datetime2, @FromDate));
DECLARE @StoredUntil datetime2 = DATEADD(year, @DateOffset, CONVERT(datetime2, @UntilDate));

/* 1. Confirm the actual server, database, login and 1C date offset. */
SELECT
    @@SERVERNAME AS sql_server,
    DB_NAME() AS [database],
    ORIGINAL_LOGIN() AS original_login,
    SYSTEM_USER AS [system_user_name],
    GETDATE() AS sql_server_time,
    @DateOffset AS date_offset,
    @FromDate AS requested_from,
    DATEADD(day, -1, @UntilDate) AS requested_to,
    @StoredFrom AS stored_from,
    @StoredUntil AS stored_until;

SELECT [Offset] AS configured_date_offset
FROM [dbo].[_YearOffset];

/*
  2. Count both document sources before and after every portal filter.
  expected_import_rows is the count that the portal must load.
*/
SELECT
    N'Путевые листы' AS source_name,
    N'dbo._Document173X1' AS sql_table,
    COUNT_BIG(*) AS total_rows,
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 THEN 1 ELSE 0 END), 0) AS not_deleted_rows,
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 AND [_Posted] = 0x01 THEN 1 ELSE 0 END), 0) AS posted_rows,
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00
                           AND [_Date_Time] >= @StoredFrom
                           AND [_Date_Time] < @StoredUntil THEN 1 ELSE 0 END), 0) AS period_rows,
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 AND [_Posted] = 0x01
                           AND [_Date_Time] >= @StoredFrom
                           AND [_Date_Time] < @StoredUntil THEN 1 ELSE 0 END), 0) AS expected_import_rows,
    CONVERT(date, DATEADD(year, -@DateOffset, MIN(CASE WHEN [_Marked] = 0x00 THEN [_Date_Time] END))) AS first_document_date,
    CONVERT(date, DATEADD(year, -@DateOffset, MAX(CASE WHEN [_Marked] = 0x00 THEN [_Date_Time] END))) AS last_document_date
FROM [dbo].[_Document173X1]
UNION ALL
SELECT
    N'Ремонтные листы',
    N'dbo._Document178X1',
    COUNT_BIG(*),
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 AND [_Posted] = 0x01 THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00
                           AND [_Date_Time] >= @StoredFrom
                           AND [_Date_Time] < @StoredUntil THEN 1 ELSE 0 END), 0),
    COALESCE(SUM(CASE WHEN [_Marked] = 0x00 AND [_Posted] = 0x01
                           AND [_Date_Time] >= @StoredFrom
                           AND [_Date_Time] < @StoredUntil THEN 1 ELSE 0 END), 0),
    CONVERT(date, DATEADD(year, -@DateOffset, MIN(CASE WHEN [_Marked] = 0x00 THEN [_Date_Time] END))),
    CONVERT(date, DATEADD(year, -@DateOffset, MAX(CASE WHEN [_Marked] = 0x00 THEN [_Date_Time] END)))
FROM [dbo].[_Document178X1];

/* 3. Show the newest documents that satisfy the exact portal filter. */
WITH import_candidates AS (
    SELECT
        N'Путевой лист' AS document_kind,
        CONVERT(varchar(32), [_IDRRef], 2) AS source_id,
        LTRIM(RTRIM([_Number])) AS document_number,
        DATEADD(year, -@DateOffset, [_Date_Time]) AS document_date,
        CONVERT(int, [_Posted]) AS posted,
        CONVERT(int, [_Marked]) AS marked,
        CONVERT(varchar(32), [_Fld1039RRef], 2) AS vehicle_ref
    FROM [dbo].[_Document173X1]
    WHERE [_Marked] = 0x00
      AND [_Posted] = 0x01
      AND [_Date_Time] >= @StoredFrom
      AND [_Date_Time] < @StoredUntil

    UNION ALL

    SELECT
        N'Ремонтный лист',
        CONVERT(varchar(32), [_IDRRef], 2),
        LTRIM(RTRIM([_Number])),
        DATEADD(year, -@DateOffset, [_Date_Time]),
        CONVERT(int, [_Posted]),
        CONVERT(int, [_Marked]),
        CONVERT(varchar(32), [_Fld1264RRef], 2)
    FROM [dbo].[_Document178X1]
    WHERE [_Marked] = 0x00
      AND [_Posted] = 0x01
      AND [_Date_Time] >= @StoredFrom
      AND [_Date_Time] < @StoredUntil
)
SELECT TOP (100)
    document_kind,
    document_number,
    document_date,
    posted,
    marked,
    vehicle_ref,
    source_id
FROM import_candidates
ORDER BY document_date DESC, document_number DESC;
