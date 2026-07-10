import { redirect } from "next/navigation";

export default function GuardRedirectPage() {
  redirect("/campaigns/new");
}
