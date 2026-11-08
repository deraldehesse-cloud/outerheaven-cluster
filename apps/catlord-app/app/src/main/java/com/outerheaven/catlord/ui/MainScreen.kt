package com.outerheaven.catlord.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.outerheaven.catlord.ui.screens.LoreViewerScreen
import com.outerheaven.catlord.ui.screens.ReaMediaScreen
import com.outerheaven.catlord.ui.screens.VoiceIngestScreen
import com.outerheaven.catlord.ui.theme.*

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    viewModel: MainViewModel,
    onRequestMicrophonePermission: () -> Unit
) {
    val currentScreen by viewModel.currentScreen.collectAsState()
    val isConnected by viewModel.isConnected.collectAsState()
    val isSyncing by viewModel.isSyncing.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Text(
                            text = "🐾 Catlord Studio",
                            style = MaterialTheme.typography.titleLarge,
                            fontWeight = FontWeight.Bold,
                            color = NeonCyan
                        )
                    }
                },
                actions = {
                    // Status indicator
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                        modifier = Modifier.padding(end = 8.dp)
                    ) {
                        Box(
                            modifier = Modifier
                                .size(10.dp)
                                .background(if (isConnected) NeonGreen else DangerRed, CircleShape)
                        )
                        Text(
                            text = if (isConnected) "Online" else "Offline",
                            fontSize = 11.sp,
                            color = TextSecondary
                        )
                    }

                    IconButton(
                        onClick = { viewModel.syncAllNotes() },
                        enabled = !isSyncing
                    ) {
                        if (isSyncing) {
                            CircularProgressIndicator(modifier = Modifier.size(20.dp), color = NeonCyan, strokeWidth = 2.dp)
                        } else {
                            Icon(Icons.Default.Sync, contentDescription = "Sync", tint = NeonCyan)
                        }
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = DarkSurface,
                    titleContentColor = TextPrimary
                )
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = DarkSurface,
                contentColor = TextPrimary
            ) {
                NavigationBarItem(
                    selected = currentScreen == AppScreen.VOICE_INGEST,
                    onClick = { viewModel.setScreen(AppScreen.VOICE_INGEST) },
                    icon = { Icon(Icons.Default.Mic, contentDescription = "Voice Ingest") },
                    label = { Text("Voice Ingest") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = DarkBackground,
                        selectedTextColor = NeonCyan,
                        indicatorColor = NeonCyan,
                        unselectedIconColor = TextSecondary,
                        unselectedTextColor = TextSecondary
                    )
                )

                NavigationBarItem(
                    selected = currentScreen == AppScreen.LORE_VIEWER,
                    onClick = { viewModel.setScreen(AppScreen.LORE_VIEWER) },
                    icon = { Icon(Icons.Default.MenuBook, contentDescription = "Lore & Docs") },
                    label = { Text("Lore & Docs") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = DarkBackground,
                        selectedTextColor = CyberViolet,
                        indicatorColor = CyberViolet,
                        unselectedIconColor = TextSecondary,
                        unselectedTextColor = TextSecondary
                    )
                )

                NavigationBarItem(
                    selected = currentScreen == AppScreen.REA_MEDIA,
                    onClick = { viewModel.setScreen(AppScreen.REA_MEDIA) },
                    icon = { Icon(Icons.Default.Science, contentDescription = "REA & Media") },
                    label = { Text("REA & Media") },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = DarkBackground,
                        selectedTextColor = AmberGold,
                        indicatorColor = AmberGold,
                        unselectedIconColor = TextSecondary,
                        unselectedTextColor = TextSecondary
                    )
                )
            }
        },
        containerColor = DarkBackground
    ) { paddingValues ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(paddingValues)
        ) {
            when (currentScreen) {
                AppScreen.VOICE_INGEST -> VoiceIngestScreen(
                    viewModel = viewModel,
                    onRequestMicrophonePermission = onRequestMicrophonePermission
                )
                AppScreen.LORE_VIEWER -> LoreViewerScreen(viewModel = viewModel)
                AppScreen.REA_MEDIA -> ReaMediaScreen(viewModel = viewModel)
            }
        }
    }
}
