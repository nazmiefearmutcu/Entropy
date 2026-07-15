import pytest
from tests.ui.test_settings_adversarial import test_bot_settings_confirm_screen_multiple_clicks

@pytest.mark.asyncio
async def test_multiple_clicks_stress(tmp_path):
    print("Running multiple clicks stress test in a loop...")
    for i in range(30):
        # Create a unique subpath within tmp_path
        sub_path = tmp_path / f"run_{i}"
        sub_path.mkdir()
        await test_bot_settings_confirm_screen_multiple_clicks(sub_path)
        print(f"Iteration {i} passed.")
