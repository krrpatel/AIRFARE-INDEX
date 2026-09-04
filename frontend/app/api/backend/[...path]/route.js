const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function GET(request, { params }) {
  const { path } = await params;
  const upstream = `${BACKEND_URL}/${path.join("/")}${new URL(request.url).search}`;
  try {
    const response = await fetch(upstream, { cache: "no-store" });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") || "application/json" },
    });
  } catch (error) {
    return Response.json({ detail: `Backend unavailable: ${error.message}` }, { status: 502 });
  }
}

export async function POST(request, { params }) {
  const { path } = await params;
  const upstream = `${BACKEND_URL}/${path.join("/")}${new URL(request.url).search}`;
  try {
    const response = await fetch(upstream, { method: "POST", headers: { "content-type": request.headers.get("content-type") || "application/json" }, body: await request.text(), cache: "no-store" });
    const body = await response.text();
    return new Response(body, { status: response.status, headers: { "content-type": response.headers.get("content-type") || "application/json" } });
  } catch (error) {
    return Response.json({ detail: `Backend unavailable: ${error.message}` }, { status: 502 });
  }
}
