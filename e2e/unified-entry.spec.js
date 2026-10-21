const { test, expect } = require("@playwright/test");
test("the former simulator opens the single main Proctix workspace", async ({
  page,
}) => {
  await page.goto("/static/decision-demo.html");
  await expect(page).toHaveURL("http://localhost:8765/");
  await expect(page.getByLabel("Organization code")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Sign in", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Simulation only", { exact: true })).toHaveCount(
    0,
  );
});
