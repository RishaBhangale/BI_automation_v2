-- Scenario: PH-BIZ-007 | Validate Next QTR Unweighted Revenue
-- Page: Next QTR Opportunities | KPI: Unweighted Revenue Next QTR Pipeline as % of STAMP
-- Default state: Next quarter unweighted pipeline revenue as % of STAMP on report load
SELECT 
    (SUM(CASE WHEN c.[Reporting Period] = 'Next Quarter' AND f.[Measure] = 'Revenue USD @ Actual Rate' THEN f.[# Value] ELSE 0 END)
     / NULLIF(SUM(f.[# Value]), 0)) * 100 AS [Unweighted Revenue Next QTR Pipeline as % of STAMP]
FROM FACTS f
INNER JOIN CALENDAR c 
    ON f.[%Date] = c.[%Date];
