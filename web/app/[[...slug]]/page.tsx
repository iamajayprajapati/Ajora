import { notFound } from "next/navigation";
import { Ajora } from "../site";

export default async function Page({ params }: { params: Promise<{ slug?: string[] }> }) {
  const route = (await params).slug ?? [];
  if (route.length > 2 || (route.length === 2 && !["events", "institutes"].includes(route[0]) && !(route[0] === "feedback" && /^\d+$/.test(route[1]))) ||
      (route.length === 1 && !["explore", "freelearn", "institutes", "my-ajora", "organizer", "review", "feedback", "about"].includes(route[0]))) {
    notFound();
  }
  return <Ajora route={route} />;
}
