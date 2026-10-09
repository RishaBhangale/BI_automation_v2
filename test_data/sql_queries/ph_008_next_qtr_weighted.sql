-- Scenario: PH-BIZ-008 | Validate Next QTR Weighted Revenue
-- Page: Next QTR Opportunities | KPI: Weighted Revenue of Next QTR Pipeline as % of STAMP
-- Default state: Next quarter weighted pipeline revenue as % of STAMP on report load
SELECT 
    (SUM(CASE WHEN c.[Reporting Period] = 'Next Quarter' AND f.[Measure] = 'Weighted Revenue USD @ Actual Rate' THEN f.[# Value] ELSE 0 END)
     / NULLIF(SUM(f.[# Value]), 0)) * 100 AS [Weighted Revenue of Next QTR Pipeline as % of STAMP]
FROM FACTS f
INNER JOIN CALENDAR c 
    ON f.[%Date] = c.[%Date];
