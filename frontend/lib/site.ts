export const SITE = {
  name: "HireWiz",
  canonicalUrl: "https://www.hirewizhq.com",
  domain: "hirewizhq.com",
  operatorName: "SAVALIYA HARSHIL YOGESHBHAI",
  operatorStatement: "operated by SAVALIYA HARSHIL YOGESHBHAI, trading as HireWiz",
  supportEmail: "work@hirewizhq.com",
  supportPhoneDisplay: "+91 72029 10650",
  supportPhoneHref: "tel:+917202910650",
  supportHours: "Monday–Sunday, 10:00 AM–10:00 PM IST",
  businessLocation: "Surat, Gujarat 395010, India",
  grievanceContactName: "Harshil Yogeshbhai Savaliya",
  grievanceContactRole: "Grievance Officer, HireWiz",
  policyEffectiveDate: "11 July 2026",
  policyVersion: "2026-07-11",
} as const;

export const publicOperatorStatement = `HireWiz is ${SITE.operatorStatement}.`;
