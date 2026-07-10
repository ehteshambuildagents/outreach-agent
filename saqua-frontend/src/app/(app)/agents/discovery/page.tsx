import { redirect } from "next/navigation";

export default function DiscoveryRedirectPage() {
  redirect("/campaigns/new");
}
