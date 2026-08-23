import json
import logging
from typing import Dict, Any
from app.config import settings

logger = logging.getLogger(__name__)


class XamlGenerator:
    """Generates UiPath Studio test .xaml files from JSON test case definitions."""

    def generate_xaml(self, test_case: Dict[str, Any]) -> str:
        """
        Convert a test case JSON to a UiPath .xaml test file.
        
        Args:
            test_case: Test case dict with id, title, priority, steps, expected_result
        
        Returns:
            XAML string representing the test workflow
        """
        test_id = test_case.get("id", "Unknown")
        title = test_case.get("title", "Untitled")
        steps = test_case.get("steps", [])
        priority = test_case.get("priority", "p2_medium")
        expected = test_case.get("expected_result", "")

        activities = []
        activities.append(f"<Activity DisplayName=\"{title}\" sap2010:WorkflowViewState.IdRef=\"test_{test_id}\">")
        activities.append(f"  <sap2010:WorkflowViewState.ViewStateSettings_Id>...")
        activities.append('    <ViewStateData />')
        activities.append("  </sap2010:WorkflowViewState.ViewStateSettings_Id>")

        # Add test metadata as a sequence
        activities.append('  <FlowStep DisplayName="Execute Test">')
        activities.append('    <FlowStep.Activities>')

        for i, step in enumerate(steps):
            action = step.get("action", "")
            selector = step.get("selector", "")
            value = step.get("value", "")
            assertion = step.get("assertion", "")

            activities.append(f'      <Sequence DisplayName="Step {i+1}: {action}">')
            if selector:
                activities.append(f'        <ui:UiBrowser Selector="{selector}" DisplayName="Target UI Element" />')
            if value:
                activities.append(f'        <ui:FormFillingWizard Value="{value}" DisplayName="Enter Value" />')
            if assertion:
                activities.append(f'        <ui:Assert True="{assertion}" DisplayName="Assert Result" />')
            if not selector and not value and not assertion:
                activities.append(f'        <ui:MessageDialog Message="{action}" DisplayName="Execute Action" />')
            activities.append('      </Sequence>')

        # Add final verification
        activities.append(f'      <ui:Assert DisplayName="Verify: {expected}" True="{expected}" />')

        activities.append('    </FlowStep.Activities>')
        activities.append('  </FlowStep>')
        activities.append('</Activity>')

        xaml_content = f'<?xml version="1.0" encoding="utf-8"?>\n'
        xaml_content += '<Activity mc:Ignorable="sad" x:Class="{x:Null}" xmlns="http://schemas.microsoft.com/netfx/2013/talk"\n'
        xaml_content += '  xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"\n'
        xaml_content += '  xmlns:sad="clr-namespace:System.Activities.Debugger;assembly=mscorlib"\n'
        xaml_content += '  xmlns:sap2010="http://schemas.microsoft.com/netfx/2010/xaml/activities/presentation"\n'
        xaml_content += '  xmlns:ui="http://schemas.uipath.com/workflow/activities"\n'
        xaml_content += '  xmlns:sap="http://schemas.microsoft.com/netfx/2009/xaml/activities/presentation"\n'
        xaml_content += f'  DisplayName="Test_{test_id}" sap2010:WorkflowViewState.IdRef="test_{test_id}">\n\n'
        xaml_content += '\n'.join(f'  {line}' if not line.startswith('  ') else line for line in activities)
        xaml_content += '\n</Activity>'

        return xaml_content

    def get_xaml_metadata(self, test_case: Dict[str, Any]) -> Dict[str, Any]:
        """Return metadata about the generated XAML for UiPath Test Cloud."""
        steps = test_case.get("steps", [])
        estimated_duration_ms = len(steps) * 5000  # 5 seconds per step estimate
        return {
            "test_id": test_case.get("id"),
            "title": test_case.get("title"),
            "priority": test_case.get("priority"),
            "step_count": len(steps),
            "estimated_duration_ms": estimated_duration_ms,
            "target_folder": settings.UIPATH_TEST_FOLDER
        }
