-- DEVELOPER-ONLY synthetic benchmark seed; never use in production.
-- Idempotently replaces only this fixture-owned table's rows.
CREATE TABLE IF NOT EXISTS synthetic_applications (application_id VARCHAR(20) PRIMARY KEY, scheme_id VARCHAR(10) NOT NULL, scheme_status VARCHAR(10) NOT NULL, annual_income_inr DECIMAL(12,2) NOT NULL, grant_amount_inr DECIMAL(12,2) NOT NULL);
DELETE FROM synthetic_applications WHERE application_id LIKE 'APP-%';
INSERT INTO synthetic_applications (application_id, scheme_id, scheme_status, annual_income_inr, grant_amount_inr) VALUES
('APP-001', 'S1', 'active', 180000.00, 10000.00),
('APP-002', 'S1', 'active', 200000.00, 15000.00),
('APP-003', 'S1', 'active', 200000.01, 9000.01),
('APP-004', 'S1', 'inactive', 120000.00, 8000.00),
('APP-005', 'S2', 'active', 150000.00, 5000.50);
