$src = 'C:\Users\Thiago Carvalho\OneDrive - Scua\Finance - 00 - Painel BI Gerencial 1\01 - Banco de Dados - Forecast\forecast_e_cenarios.xlsx'
$dst = 'C:\Users\Thiago Carvalho\OneDrive - Scua\Finance - 00 - Painel BI Gerencial 1\01 - Banco de Dados - Forecast\forecast_e_cenarios_funcional.xlsx'

$excel = New-Object -ComObject Excel.Application
$excel.Visible = $false
$excel.DisplayAlerts = $false
$excel.Calculation = -4135

function Release-ComObject($obj) {
    if ($null -ne $obj) {
        [void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($obj)
    }
}

try {
    $srcWb = $excel.Workbooks.Open($src, 0, $true)
    $srcSim = $srcWb.Worksheets.Item('Simulador')

    if (Test-Path $dst) {
        Remove-Item $dst -Force
    }

    $wb = $excel.Workbooks.Add()
    while ($wb.Worksheets.Count -lt 3) {
        [void]$wb.Worksheets.Add()
    }
    while ($wb.Worksheets.Count -gt 3) {
        $wb.Worksheets.Item($wb.Worksheets.Count).Delete()
    }

    $wsSim = $wb.Worksheets.Item(1)
    $wsBase = $wb.Worksheets.Item(2)
    $wsInst = $wb.Worksheets.Item(3)
    $wsSim.Name = 'Simulador'
    $wsBase.Name = 'BI_Base'
    $wsInst.Name = 'Instrucoes'

    $xlSrcRange = 1
    $xlYes = 1

    $wsSim.Range('A1').Value2 = 'Simulador de Cenários'
    $wsSim.Range('A2').Value2 = 'Preencha somente as células amarelas. Um registro por linha.'
    $wsSim.Range('A3').Value2 = 'Use Entrada ou Saída. Informe mês inicial e mês final. Se for um único mês, repita o mesmo mês no início e no fim.'
    $wsSim.Range('A6').Resize(1, 6).Value2 = @('Identificação livre', 'Entrada ou Saída', 'Início', 'Fim', 'Valor mensal', 'Observações')
    $wsSim.Range('G6').Resize(1, 3).Value2 = @('DuracaoMeses', 'LinhaInicialBI', 'LinhaFinalBI')

    $srcData = $srcSim.Range('A7:F206').Value2
    $wsSim.Range('A7:F206').Value2 = $srcData

    $wsSim.Range('A1').Font.Bold = $true
    $wsSim.Range('A1').Font.Size = 16
    $wsSim.Range('A6:F6').Font.Bold = $true
    $wsSim.Range('A6:F206').Borders.LineStyle = 1
    $wsSim.Range('B7:E206').Interior.Color = 65535
    $wsSim.Range('C7:D206').NumberFormat = 'mmm/yyyy'
    $wsSim.Range('E7:E206').NumberFormat = '#,##0.00'
    $wsSim.Columns('A:F').AutoFit()
    $wsSim.Columns('G:I').Hidden = $true

    $wsSim.Range('B7:B206').Validation.Delete()
    $wsSim.Range('B7:B206').Validation.Add(3, 1, 1, 'Entrada,Saída')

    $wsSim.Range('G7:G206').FormulaR1C1 = '=IF(OR(RC1="",RC3=""),0,DATEDIF(EOMONTH(RC3,-1)+1,EOMONTH(IF(RC4="",RC3,RC4),0),"m")+1)'
    $wsSim.Range('H7').FormulaR1C1 = '=IF(RC7=0,"",1)'
    $wsSim.Range('H8:H206').FormulaR1C1 = '=IF(RC7=0,"",MAX(R7C9:R[-1]C9)+1)'
    $wsSim.Range('I7:I206').FormulaR1C1 = '=IF(RC7=0,"",RC8+RC7-1)'

    $wsBase.Range('A1:G1').Value2 = @('data_mes', 'ano', 'mes_num', 'mes_nome', 'ano_mes', 'tipo_fluxo', 'valor')
    $wsBase.Range('A1:G1').Font.Bold = $true
    $wsBase.Range('A1:G1').Interior.Color = 15773696

    $wsBase.Range('A2:A12001').FormulaR1C1 = '=IF(ROW()-1>MAX(Simulador!R7C9:R206C9),"",EDATE(INDEX(Simulador!R7C3:R206C3,MATCH(ROW()-1,Simulador!R7C8:R206C8,1)),ROW()-INDEX(Simulador!R7C8:R206C8,MATCH(ROW()-1,Simulador!R7C8:R206C8,1))-1))'
    $wsBase.Range('B2:B12001').FormulaR1C1 = '=IF(RC1="","",YEAR(RC1))'
    $wsBase.Range('C2:C12001').FormulaR1C1 = '=IF(RC1="","",MONTH(RC1))'
    $wsBase.Range('D2:D12001').FormulaR1C1 = '=IF(RC1="","",TEXT(RC1,"mmmm"))'
    $wsBase.Range('E2:E12001').FormulaR1C1 = '=IF(RC1="","",TEXT(RC1,"yyyy-mm"))'
    $wsBase.Range('F2:F12001').FormulaR1C1 = '=IF(RC1="","",INDEX(Simulador!R7C2:R206C2,MATCH(ROW()-1,Simulador!R7C8:R206C8,1)))'
    $wsBase.Range('G2:G12001').FormulaR1C1 = '=IF(RC1="","",INDEX(Simulador!R7C5:R206C5,MATCH(ROW()-1,Simulador!R7C8:R206C8,1)))'

    $wsBase.Range('A2:A12001').NumberFormat = 'dd/mm/yyyy'
    $wsBase.Range('G2:G12001').NumberFormat = '#,##0.00'
    $wsBase.Columns('A:G').AutoFit()

    $usedBase = $wsBase.Range('A1:G12001')
    [void]$wsBase.ListObjects.Add($xlSrcRange, $usedBase, $null, $xlYes)

    $wsInst.Range('A1').Value2 = 'Instruções rápidas'
    $wsInst.Range('A2:B8').Value2 = @(
        @('Objetivo', 'Esta planilha serve apenas para imputar lançamentos simples que serão levados ao Power BI.'),
        @('Aba Simulador', 'Preencha: identificação livre, se é Entrada ou Saída, mês inicial, mês final, valor mensal e, se quiser, observações.'),
        @('Identificação livre', 'Serve para organização de quem estiver preenchendo e também ajuda na conferência.'),
        @('Início e fim', 'A aba BI_Base explode automaticamente todos os meses entre início e fim. Se for um único mês, repita o mesmo mês.'),
        @('Tipo', 'Use apenas Entrada ou Saída.'),
        @('Aba BI_Base', 'Ela gera automaticamente a base para o Power BI com linhas contínuas, sem blocos vazios.'),
        @('Importante', 'Edite apenas a aba Simulador. Depois salve, feche o Excel e atualize o Power BI.')
    )
    $wsInst.Range('A1').Font.Bold = $true
    $wsInst.Range('A1').Font.Size = 14
    $wsInst.Columns('A:B').AutoFit()

    $excel.CalculateFull()
    $excel.Calculation = -4105

    $wb.SaveAs($dst, 51)
    $wb.Close($true)
    $srcWb.Close($false)
    Write-Output $dst
}
finally {
    $excel.Quit()
    Release-ComObject $wsInst
    Release-ComObject $wsBase
    Release-ComObject $wsSim
    Release-ComObject $wb
    Release-ComObject $srcSim
    Release-ComObject $srcWb
    Release-ComObject $excel
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
