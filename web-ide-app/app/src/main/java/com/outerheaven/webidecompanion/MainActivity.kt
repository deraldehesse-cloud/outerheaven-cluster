package com.outerheaven.webidecompanion

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.viewModels
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Analytics
import androidx.compose.material.icons.filled.Dashboard
import androidx.compose.material.icons.filled.Dns
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import com.outerheaven.webidecompanion.ui.screens.DashboardScreen
import com.outerheaven.webidecompanion.ui.screens.DropzoneScreen
import com.outerheaven.webidecompanion.ui.screens.FindingsScreen
import com.outerheaven.webidecompanion.ui.screens.SandboxScreen
import com.outerheaven.webidecompanion.ui.theme.AccentCyan
import com.outerheaven.webidecompanion.ui.theme.DarkBackground
import com.outerheaven.webidecompanion.ui.theme.DarkSurface
import com.outerheaven.webidecompanion.ui.theme.MetalCompanionTheme
import com.outerheaven.webidecompanion.ui.theme.TextMuted
import com.outerheaven.webidecompanion.ui.theme.TextSecondary
import com.outerheaven.webidecompanion.ui.viewmodel.MainViewModel

enum class NavigationItem(val title: String, val icon: ImageVector) {
    DASHBOARD("Dashboard", Icons.Default.Dashboard),
    SANDBOX("Sandbox", Icons.Default.Dns),
    DROPZONE("Dropzone", Icons.Default.Folder),
    FINDINGS("Findings", Icons.Default.Analytics)
}

class MainActivity : ComponentActivity() {

    private val viewModel: MainViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        setContent {
            MetalCompanionTheme {
                var selectedTab by remember { mutableStateOf(NavigationItem.DASHBOARD) }

                Scaffold(
                    modifier = Modifier.fillMaxSize(),
                    containerColor = DarkBackground,
                    bottomBar = {
                        NavigationBar(
                            containerColor = DarkSurface
                        ) {
                            NavigationItem.values().forEach { item ->
                                val isSelected = selectedTab == item
                                NavigationBarItem(
                                    selected = isSelected,
                                    onClick = { selectedTab = item },
                                    icon = {
                                        Icon(
                                            imageVector = item.icon,
                                            contentDescription = item.title
                                        )
                                    },
                                    label = { Text(item.title) },
                                    colors = NavigationBarItemDefaults.colors(
                                        selectedIconColor = DarkBackground,
                                        selectedTextColor = AccentCyan,
                                        indicatorColor = AccentCyan,
                                        unselectedIconColor = TextSecondary,
                                        unselectedTextColor = TextMuted
                                    )
                                )
                            }
                        }
                    }
                ) { innerPadding ->
                    when (selectedTab) {
                        NavigationItem.DASHBOARD -> DashboardScreen(
                            viewModel = viewModel,
                            modifier = Modifier.padding(innerPadding)
                        )
                        NavigationItem.SANDBOX -> SandboxScreen(
                            viewModel = viewModel,
                            modifier = Modifier.padding(innerPadding)
                        )
                        NavigationItem.DROPZONE -> DropzoneScreen(
                            viewModel = viewModel,
                            modifier = Modifier.padding(innerPadding)
                        )
                        NavigationItem.FINDINGS -> FindingsScreen(
                            viewModel = viewModel,
                            modifier = Modifier.padding(innerPadding)
                        )
                    }
                }
            }
        }
    }
}
