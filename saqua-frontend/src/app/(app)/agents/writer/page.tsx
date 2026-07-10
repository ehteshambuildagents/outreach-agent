import { redirect } from "next/navigation";

export default function WriterRedirectPage() {
  redirect("/campaigns/new");
}
