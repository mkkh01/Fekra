export class HUDController {
  updateYear(year: number) {
    const el = document.getElementById('yearDisplay');
    if (el) el.textContent = String(year);
  }

  updateGold(amount: number) {
    const el = document.getElementById('goldDisplay');
    if (el) el.textContent = amount.toLocaleString();
  }

  updateFood(amount: number) {
    const el = document.getElementById('foodDisplay');
    if (el) el.textContent = String(Math.round(amount));
  }

  updateWars(count: number) {
    const el = document.getElementById('warDisplay');
    if (el) el.textContent = String(count);
  }

  updateNations(count: number) {
    const el = document.getElementById('nationDisplay');
    if (el) el.textContent = String(count);
  }
}
