import { redirect } from "next/navigation";

export default function LegacyJobsPage() {
  redirect("/workspace?new=1");
}
