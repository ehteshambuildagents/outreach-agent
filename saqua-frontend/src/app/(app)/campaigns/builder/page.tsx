import { redirect } from "next/navigation";

export default function CampaignBuilderRedirectPage() {
  redirect("/campaigns/new");
}
