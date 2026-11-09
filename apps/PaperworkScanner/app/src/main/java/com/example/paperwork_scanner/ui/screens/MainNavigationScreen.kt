package com.example.paperwork_scanner.ui.screens

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountBalanceWallet
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Shield
import androidx.compose.material.icons.filled.Work
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.example.paperwork_scanner.data.AuthPreferencesRepository

enum class NavigationTab(val title: String) {
    SCANNER("Scanner"),
    FINANZ_RADAR("Finanz-Radar"),
    PHOBIE_SCHILD("Phobie-Schild"),
    BEWERBUNG("Bewerbung")
}

@Composable
fun MainNavigationScreen(
    authRepository: AuthPreferencesRepository,
    initialTab: NavigationTab = NavigationTab.SCANNER
) {
    var currentTab by rememberSaveable { mutableStateOf(initialTab) }

    Scaffold(
        bottomBar = {
            NavigationBar(
                tonalElevation = NavigationBarDefaults.Elevation
            ) {
                // 1. Scanner
                NavigationBarItem(
                    selected = currentTab == NavigationTab.SCANNER,
                    onClick = { currentTab = NavigationTab.SCANNER },
                    icon = {
                        Icon(
                            imageVector = Icons.Default.Description,
                            contentDescription = "Scanner"
                        )
                    },
                    label = {
                        Text(
                            text = "Scanner",
                            fontSize = 11.sp,
                            fontWeight = if (currentTab == NavigationTab.SCANNER) FontWeight.Bold else FontWeight.Normal
                        )
                    }
                )

                // 2. Finanz-Radar
                NavigationBarItem(
                    selected = currentTab == NavigationTab.FINANZ_RADAR,
                    onClick = { currentTab = NavigationTab.FINANZ_RADAR },
                    icon = {
                        Icon(
                            imageVector = Icons.Default.AccountBalanceWallet,
                            contentDescription = "Finanz-Radar"
                        )
                    },
                    label = {
                        Text(
                            text = "Finanz-Radar",
                            fontSize = 11.sp,
                            fontWeight = if (currentTab == NavigationTab.FINANZ_RADAR) FontWeight.Bold else FontWeight.Normal
                        )
                    }
                )

                // 3. Phobie-Schild
                NavigationBarItem(
                    selected = currentTab == NavigationTab.PHOBIE_SCHILD,
                    onClick = { currentTab = NavigationTab.PHOBIE_SCHILD },
                    icon = {
                        Icon(
                            imageVector = Icons.Default.Shield,
                            contentDescription = "Phobie-Schild"
                        )
                    },
                    label = {
                        Text(
                            text = "Phobie-Schild",
                            fontSize = 11.sp,
                            fontWeight = if (currentTab == NavigationTab.PHOBIE_SCHILD) FontWeight.Bold else FontWeight.Normal
                        )
                    }
                )

                // 4. Bewerbungs-Assistent
                NavigationBarItem(
                    selected = currentTab == NavigationTab.BEWERBUNG,
                    onClick = { currentTab = NavigationTab.BEWERBUNG },
                    icon = {
                        Icon(
                            imageVector = Icons.Default.Work,
                            contentDescription = "Bewerbung"
                        )
                    },
                    label = {
                        Text(
                            text = "Bewerbung",
                            fontSize = 11.sp,
                            fontWeight = if (currentTab == NavigationTab.BEWERBUNG) FontWeight.Bold else FontWeight.Normal
                        )
                    }
                )
            }
        }
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
        ) {
            when (currentTab) {
                NavigationTab.SCANNER -> {
                    ScannerMainScreen(
                        authRepository = authRepository,
                        onNavigateToEmailReview = {
                            currentTab = NavigationTab.PHOBIE_SCHILD
                        }
                    )
                }
                NavigationTab.FINANZ_RADAR -> {
                    FinanzRadarScreen()
                }
                NavigationTab.PHOBIE_SCHILD -> {
                    PhobieSchildScreen()
                }
                NavigationTab.BEWERBUNG -> {
                    BewerbungScreen(
                        authRepository = authRepository
                    )
                }
            }
        }
    }
}
